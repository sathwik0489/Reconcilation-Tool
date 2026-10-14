import json
import re
from datetime import date,datetime
from decimal import Decimal,InvalidOperation
from fastapi import HTTPException
from sqlalchemy import Table,MetaData,Column,Integer,BigInteger,String,Text,Numeric,Date,DateTime,Boolean,select,inspect,delete,func
from app.models import (RegisteredTable,TableColumn,MedallionLayer,IntegrationResource,
    IntegrationConnection,IntegrationSyncConfiguration,IntegrationSyncRun,IngestionConfiguration,IngestionRun)
from app.schemas.integrations import IDENTIFIER
from app.connectors.integrations import postgres_engine,records,infer_schema,utcnow,application_slug
from app.services.audit import audit

TYPES={'INTEGER':Integer,'BIGINT':BigInteger,'VARCHAR':lambda:String(255),'TEXT':Text,
       'DECIMAL':lambda:Numeric(30,10),'DATE':Date,'TIMESTAMP':lambda:DateTime(timezone=True),'BOOLEAN':Boolean}

def column_type(dtype):
    normalized=dtype.upper()
    if normalized in TYPES:return TYPES[normalized]()
    if normalized.startswith(('NUMERIC','DECIMAL','FLOAT','DOUBLE','REAL')):return Numeric(30,10)
    if 'INT' in normalized:return BigInteger()
    if normalized.startswith('BOOL'):return Boolean()
    if normalized.startswith('TIMESTAMP'):return DateTime(timezone=True)
    if normalized=='DATE':return Date()
    return Text()

def ensure_registered(db,connection,resource,classification=None):
    if resource.registered_table_id:return db.get(RegisteredTable,resource.registered_table_id)
    pg=application_slug(db,connection)=='postgresql'
    schema,name=resource.path.split('.',1) if pg else ('api',resource.name)
    config=connection.configuration
    row=db.scalar(select(RegisteredTable).where(RegisteredTable.connection_id==connection.database_connection_id,
        RegisteredTable.schema_name==schema,RegisteredTable.table_name==name))
    if not row:
        row=RegisteredTable(organization_id=connection.organization_id,connection_id=connection.database_connection_id,database_name=config.get('database_name') or application_slug(db,connection),
            schema_name=schema,table_name=name,classification=classification or config['classification'])
        db.add(row);db.flush()
    resource.registered_table_id=row.id
    replace_columns(db,row,resource.schema_snapshot)
    return row

def replace_columns(db,row,fields):
    # Keep referenced column IDs stable when schema changes.
    existing={c.name:c for c in db.scalars(select(TableColumn).where(TableColumn.table_id==row.id)).all()}
    for i,field in enumerate(fields):
        item=existing.get(field['name'])
        if not item:
            ordinal=max([c.ordinal for c in existing.values()],default=0)+1
            item=TableColumn(table_id=row.id,name=field['name'],data_type=field['data_type'],ordinal=ordinal,nullable=field.get('nullable',True),primary_key=field.get('primary_key',False))
            db.add(item)
            existing[item.name]=item
        else:item.data_type=field['data_type'];item.nullable=field.get('nullable',True)

def coerce(value,column):
    if value is None:return None
    try:
        if isinstance(column.type,Boolean):
            if isinstance(value,bool):return value
            if str(value).lower() in ['true','1']:return True
            if str(value).lower() in ['false','0']:return False
            raise ValueError()
        if isinstance(column.type,(Integer,BigInteger)):
            number=Decimal(str(value))
            if not number.is_finite() or number!=number.to_integral_value():raise ValueError()
            return int(number)
        if isinstance(column.type,Numeric):
            number=Decimal(str(value))
            if not number.is_finite():raise ValueError()
            return number
        if isinstance(column.type,DateTime):return datetime.fromisoformat(str(value).replace('Z','+00:00'))
        if isinstance(column.type,Date):return date.fromisoformat(str(value))
        return json.dumps(value,sort_keys=True) if isinstance(value,(dict,list)) else str(value)
    except (ValueError,TypeError,InvalidOperation):raise HTTPException(422,f'Value cannot be converted for column {column.name}') from None

def ingest(db,source,resource,destination,payload,user):
    if not payload.confirmed:raise HTTPException(422,'Confirm destination changes before ingestion')
    if not all(re.fullmatch(IDENTIFIER,c) for c in payload.columns):raise HTTPException(422,'Selected column names must be valid identifiers')
    if payload.configuration_id:
        config=db.scalar(select(IntegrationSyncConfiguration).where(IntegrationSyncConfiguration.id==payload.configuration_id).with_for_update())
        if not config or config.connection_id!=source.id or config.resource_id!=resource.id or config.destination_id!=destination.id:
            raise HTTPException(404,'Sync configuration not found for this integration')
        compare=payload.model_dump(mode='json',exclude={'configuration_id','confirmed'})
        if config.configuration!=compare:raise HTTPException(422,'Incremental reruns must retain the saved destination and selected columns')
    else:
        config=IntegrationSyncConfiguration(connection_id=source.id,resource_id=resource.id,destination_id=destination.id,
            configuration=payload.model_dump(mode='json',exclude={'configuration_id','confirmed'}))
        db.add(config);db.flush()
    run=IntegrationSyncRun(connection_id=source.id,configuration_id=config.id,executed_by=user.id,status='running',record_count=0,started_at=utcnow())
    db.add(run);db.commit()
    try:
        rows=records(db,source,resource,size=100,all_rows=True)
        schema=infer_schema(rows) if rows else resource.schema_snapshot
        fields={f['name']:f for f in schema}
        if not set(payload.columns).issubset(fields):raise HTTPException(422,'Selected columns are absent from the resource schema')
        if resource.schema_snapshot and resource.schema_snapshot!=schema and rows:
            raise HTTPException(422,'Source schema changed. Preview and approve the latest schema before ingestion.')
        if payload.mode=='incremental':
            key=payload.watermark_column
            if any(r.get(key) is None for r in rows):raise HTTPException(422,'Watermark values must not be null')
            dtype=fields[key]['data_type']
            convert=lambda v:Decimal(str(v)) if dtype in ['BIGINT','DECIMAL','INTEGER'] else str(v)
            # Filter against the destination ledger inside the same write transaction.
        with postgres_engine(db,destination) as engine,engine.begin() as conn:
            if payload.schema_name!=destination.configuration['schema_name']:raise HTTPException(422,'Destination schema must match the authorized connection schema')
            inspector=inspect(conn)
            if payload.schema_name not in inspector.get_schema_names():raise HTTPException(422,'Create/authorize the destination schema in PostgreSQL first')
            # Serialize writes to this destination table across connections/runs.
            conn.execute(select(func.pg_advisory_xact_lock(func.hashtext(payload.schema_name+'.'+payload.table_name))))
            ledger=Table('_reconcile_sync_ledger',MetaData(),Column('configuration_id',String(36),primary_key=True),Column('watermark',Text),Column('target_table',Text,nullable=False),schema=payload.schema_name)
            ledger_value=None
            watermark=config.watermark_value
            if payload.mode=='incremental':
                ledger.create(conn,checkfirst=True)
                ledger_value=conn.execute(select(ledger.c.watermark).where(ledger.c.configuration_id==str(config.id))).scalar_one_or_none()
                if ledger_value is not None and (watermark is None or convert(ledger_value)>convert(watermark)):watermark=ledger_value
                if watermark is not None:rows=[r for r in rows if convert(r[key])>convert(watermark)]
            exists=inspector.has_table(payload.table_name,schema=payload.schema_name)
            if exists:
                table=Table(payload.table_name,MetaData(),schema=payload.schema_name,autoload_with=conn)
                if not set(payload.columns).issubset(table.c.keys()):raise HTTPException(422,'Destination schema lacks selected columns')
            else:
                table=Table(payload.table_name,MetaData(),*[Column(c,column_type(fields[c]['data_type']),nullable=True) for c in payload.columns],schema=payload.schema_name)
                table.create(conn)
            if payload.mode=='full':conn.execute(delete(table))
            values=[{c:coerce(row.get(c),table.c[c]) for c in payload.columns} for row in rows]
            for start in range(0,len(values),500):
                conn.execute(table.insert(),values[start:start+500])
            if payload.mode=='incremental':
                if rows:watermark=str(max((r[payload.watermark_column] for r in rows),key=convert))
                from sqlalchemy.dialects.postgresql import insert as pg_insert
                statement=pg_insert(ledger).values(configuration_id=str(config.id),watermark=watermark,target_table=payload.table_name)
                conn.execute(statement.on_conflict_do_update(index_elements=['configuration_id'],set_={'watermark':watermark,'target_table':payload.table_name}))
                config.watermark_value=watermark
            destination_schema=[{'name':c.name,'data_type':str(c.type),'nullable':c.nullable,'primary_key':c.primary_key} for c in table.columns]
        resource.schema_snapshot=schema;source_table=ensure_registered(db,source,resource)
        destination_resource=db.scalar(select(IntegrationResource).where(IntegrationResource.connection_id==destination.id,IntegrationResource.path==payload.schema_name+'.'+payload.table_name))
        if not destination_resource:
            destination_resource=IntegrationResource(connection_id=destination.id,name=payload.table_name,path=payload.schema_name+'.'+payload.table_name,record_path='',pagination={},schema_snapshot=destination_schema,schema_version=1,available_filters=[])
            db.add(destination_resource);db.flush()
        destination_resource.schema_snapshot=destination_schema
        target=ensure_registered(db,destination,destination_resource,'target')
        target.layer_id=db.scalar(select(MedallionLayer.id).where(MedallionLayer.name==payload.layer))
        config.registered_table_id=target.id
        if payload.mode=='incremental' and rows:
            config.watermark_value=str(max((r[payload.watermark_column] for r in rows),key=convert))
        ingestion_config=IngestionConfiguration(organization_id=source.organization_id,name=(source.name+' → '+target.table_name)[:100],source_connection_id=source.database_connection_id,
            destination_connection_id=destination.database_connection_id,source_table_id=source_table.id,destination_table_id=target.id,
            ingestion_type='external',mode=payload.mode,watermark_column=payload.watermark_column,watermark_value=config.watermark_value,owner_id=user.id,
            options={'integration_sync_configuration_id':str(config.id)})
        db.add(ingestion_config);db.flush()
        db.add(IngestionRun(organization_id=source.organization_id,configuration_id=ingestion_config.id,status='succeeded',record_count=len(rows),started_at=run.started_at,completed_at=utcnow(),executed_by=user.id))
        run.status='succeeded';run.record_count=len(rows);run.completed_at=utcnow();source.last_successful_sync=utcnow()
        audit(db,'integration.ingested',user.id,'integration',source.id,{'rows':len(rows),'target_table_id':str(target.id)})
        db.commit()
        return {'run_id':run.id,'configuration_id':config.id,'table_id':target.id,'record_count':len(rows),'status':'succeeded','watermark':config.watermark_value}
    except Exception as exc:
        db.rollback()
        run=db.get(IntegrationSyncRun,run.id);run.status='failed';run.completed_at=utcnow()
        message=exc.detail if isinstance(exc,HTTPException) else 'Ingestion failed; check schema and destination permissions'
        run.error_details=str(message);source=db.get(IntegrationConnection,source.id);source.last_failed_sync=utcnow()
        from app.models import IntegrationError
        db.add(IntegrationError(connection_id=source.id,run_id=run.id,operation='ingest',error_code='sync_failed',message=str(message)))
        db.commit()
        raise HTTPException(422,str(message)) from None
