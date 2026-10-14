import time
from uuid import UUID
from fastapi import APIRouter,Depends,HTTPException,Query
from fastapi.encoders import jsonable_encoder
from fastapi.responses import StreamingResponse
from sqlalchemy import select,Table,MetaData,Column,inspect
from sqlalchemy.schema import CreateTable
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.core.config import get_settings
from app.models import (ExternalApplication,IntegrationConnection,IntegrationResource,RegisteredTable,TableColumn,MedallionLayer,
    TableMapping,MappingEdge,MappingSource,ColumnMapping,ReconciliationJob,ReconciliationRule,ReconciliationRun,ReconciliationResult,ReconciliationDifference,User)
from app.api.integrations import owned,writer,mapper,selected_resource
from app.core.security import current_user
from app.schemas.integrations import TableInput
from app.schemas.reconciliation import MappingInput,RunInput,Branch
from app.services.integration_data import ensure_registered,column_type
from app.connectors.integrations import postgres_engine,records,utcnow
from app.reconciliation.engine import joined,compare,prepare
from app.services.audit import audit

router=APIRouter(prefix='/api',tags=['Data workflows'])

def table_connection(db,user,table_id):
    row=db.get(RegisteredTable,table_id)
    if not row or row.organization_id!=user.organization_id:raise HTTPException(404,'Table not found')
    connection=db.scalar(select(IntegrationConnection).where(IntegrationConnection.database_connection_id==row.connection_id))
    if not connection:raise HTTPException(422,'Table is not attached to an executable integration')
    owned(db,user,connection.id)
    resource=db.scalar(select(IntegrationResource).where(IntegrationResource.registered_table_id==row.id))
    if not resource:raise HTTPException(422,'Table has no registered integration resource')
    return row,connection,resource

def definition(payload):
    if not payload.columns:raise HTTPException(422,'Add at least one column')
    if len(set(c.name for c in payload.columns))!=len(payload.columns):raise HTTPException(422,'Column names must be unique')
    return Table(payload.table_name,MetaData(),*[Column(c.name,column_type(c.data_type),primary_key=c.primary_key,nullable=False if c.primary_key else c.nullable) for c in payload.columns],schema=payload.schema_name)

@router.post('/tables/ddl-preview')
def ddl(payload:TableInput,db:Session=Depends(get_db),user:User=Depends(writer)):
    connection=owned(db,user,payload.connection_id)
    with postgres_engine(db,connection) as engine:
        return {'statement':str(CreateTable(definition(payload)).compile(engine)), 'action':payload.action}

@router.post('/tables',status_code=201)
def create_table(payload:TableInput,db:Session=Depends(get_db),user:User=Depends(writer)):
    connection=owned(db,user,payload.connection_id)
    if payload.schema_name!=connection.configuration['schema_name']:raise HTTPException(422,'Use the authorized schema')
    if not payload.confirmed:raise HTTPException(422,'Confirm table creation or registration')
    with postgres_engine(db,connection) as engine,engine.begin() as conn:
        inspector=inspect(conn)
        if payload.schema_name not in inspector.get_schema_names():raise HTTPException(422,'Authorized schema does not exist')
        exists=inspector.has_table(payload.table_name,schema=payload.schema_name)
        if payload.action=='create':
            if exists:raise HTTPException(409,'Table already exists; register it instead')
            definition(payload).create(conn)
        elif not exists:raise HTTPException(404,'Existing table not found')
        fields=[{'name':c['name'],'data_type':str(c['type']),'nullable':c['nullable']} for c in inspect(conn).get_columns(payload.table_name,schema=payload.schema_name)]
    resource=db.scalar(select(IntegrationResource).where(IntegrationResource.connection_id==connection.id,IntegrationResource.name==payload.table_name))
    if not resource:
        resource=IntegrationResource(connection_id=connection.id,name=payload.table_name,path=payload.schema_name+'.'+payload.table_name,record_path='',pagination={},schema_snapshot=fields,schema_version=1,available_filters=[])
        db.add(resource);db.flush()
    resource.schema_snapshot=fields
    row=ensure_registered(db,connection,resource)
    row.layer_id=db.scalar(select(MedallionLayer.id).where(MedallionLayer.name==payload.layer))
    audit(db,'table.'+payload.action,user.id,'table',row.id);db.commit()
    return {'table_id':row.id,'table_name':row.table_name,'schema_name':row.schema_name}

@router.patch('/tables/{id}/layer')
def layer(id:UUID,layer:str=Query(...,pattern='^(bronze|silver|gold)$'),db:Session=Depends(get_db),user:User=Depends(writer)):
    row,_,_=table_connection(db,user,id)
    row.layer_id=db.scalar(select(MedallionLayer.id).where(MedallionLayer.name==layer))
    audit(db,'table.layer_updated',user.id,'table',id);db.commit()
    return {'layer':layer}

@router.get('/tables/{id}/preview')
def preview_table(id:UUID,page:int=Query(1,ge=1),page_size:int=Query(25,ge=1,le=100),db:Session=Depends(get_db),user:User=Depends(current_user)):
    _,connection,resource=table_connection(db,user,id)
    return {'items':records(db,connection,resource,page,page_size),'page':page,'page_size':page_size}

@router.get('/workflows/tables')
def available(search:str=Query('',max_length=100),db:Session=Depends(get_db),user:User=Depends(current_user)):
    query=select(RegisteredTable,IntegrationConnection).join(IntegrationConnection,RegisteredTable.connection_id==IntegrationConnection.database_connection_id).where(IntegrationConnection.status!='revoked',RegisteredTable.organization_id==user.organization_id)
    if search:query=query.where(RegisteredTable.table_name.icontains(search,autoescape=True))
    result=[]
    layers={l.id:l.name for l in db.scalars(select(MedallionLayer))}
    applications={a.id:a.slug for a in db.scalars(select(ExternalApplication))}
    for table,connection in db.execute(query.order_by(RegisteredTable.table_name).limit(500)):
        fields=db.scalars(select(TableColumn).where(TableColumn.table_id==table.id).order_by(TableColumn.ordinal)).all()
        result.append({'id':table.id,'name':table.table_name,'classification':table.classification,'schema_name':table.schema_name,'layer_id':table.layer_id,'layer':layers.get(table.layer_id),'integration_id':connection.id,'connection_name':connection.name,'application':applications.get(connection.application_id),
            'columns':[{'name':c.name,'data_type':c.data_type,'primary_key':c.primary_key,'qualified_name':table.table_name+'.'+c.name} for c in fields]})
    return result

@router.post('/mappings',status_code=201)
def save_mapping(payload:MappingInput,db:Session=Depends(get_db),user:User=Depends(mapper)):
    kind='one_to_many' if len(payload.branches)>1 else 'many_to_one' if len(payload.branches[0].source_table_ids)>1 else 'one_to_one'
    mapping=TableMapping(organization_id=user.organization_id,name=payload.name,mapping_type=kind,owner_id=user.id);db.add(mapping);db.flush()
    job=ReconciliationJob(organization_id=user.organization_id,name=payload.name,mapping_id=mapping.id,owner_id=user.id);db.add(job);db.flush()
    for branch in payload.branches:
        target,_,_=table_connection(db,user,branch.target_table_id)
        target_columns={c.name:c for c in db.scalars(select(TableColumn).where(TableColumn.table_id==target.id))}
        sources={}
        source_names=set()
        ordered_sources=[]
        for id in branch.source_table_ids:
            table,_,_=table_connection(db,user,id)
            if table.table_name in source_names:raise HTTPException(422,'Source table names must be distinct for qualified column mapping')
            source_names.add(table.table_name)
            ordered_sources.append(table.table_name)
            for col in db.scalars(select(TableColumn).where(TableColumn.table_id==id)):sources[table.table_name+'.'+col.name]=col
        for column in branch.columns:
            if column.source not in sources or column.target not in target_columns:raise HTTPException(422,'Mapped columns must belong to selected tables')
        for agg in branch.aggregations:
            if agg.source not in sources or agg.target not in target_columns:raise HTTPException(422,'Aggregation columns must belong to selected tables')
        for index,join in enumerate(branch.joins):
            if join.left not in sources or join.right not in sources or not join.right.startswith(ordered_sources[index+1]+'.') or not any(join.left.startswith(n+'.') for n in ordered_sources[:index+1]):
                raise HTTPException(422,'Join each additional source to an earlier selected source using existing columns')
        for condition in branch.source_filters:
            if condition.column not in sources:raise HTTPException(422,'Source filter column is not selected')
        for condition in branch.target_filters:
            if condition.column not in target_columns:raise HTTPException(422,'Target filter column is not selected')
        if not set(branch.required_columns).issubset(target_columns):raise HTTPException(422,'Required columns must belong to the target')
        edge=MappingEdge(mapping_id=mapping.id,target_table_id=target.id,business_keys=branch.business_keys,
            join_conditions=[j.model_dump() for j in branch.joins],filters={'source':[f.model_dump() for f in branch.source_filters],'target':[f.model_dump() for f in branch.target_filters]},aggregations=[a.model_dump() for a in branch.aggregations])
        db.add(edge);db.flush()
        for id in branch.source_table_ids:db.add(MappingSource(edge_id=edge.id,source_table_id=id))
        for column in branch.columns:db.add(ColumnMapping(edge_id=edge.id,source_column_id=sources[column.source].id,target_column_id=target_columns[column.target].id,transformation=column.model_dump()))
        db.add(ReconciliationRule(job_id=job.id,edge_id=edge.id,rule_type='branch_comparison',configuration=branch.model_dump(mode='json')))
    audit(db,'mapping.created',user.id,'mapping',mapping.id);db.commit()
    return {'id':mapping.id,'job_id':job.id,'mapping_type':kind}

def source_rows(db,user,branch,cache):
    """Read each source table once, qualify its columns by table name, and join them as the branch configures."""
    datasets=[]
    for id in branch.source_table_ids:
        if id not in cache:
            table,connection,resource=table_connection(db,user,id)
            raw=records(db,connection,resource,size=100,all_rows=True)
            cache[id]=[{**{table.table_name+'.'+k:v for k,v in row.items()},'__ids':{str(id)+':'+str(i)}} for i,row in enumerate(raw)]
        datasets.append(cache[id])
    return joined(datasets,branch.joins,get_settings().integration_max_rows)

@router.post('/mappings/preview')
def preview_branch(payload:Branch,limit:int=Query(50,ge=1,le=200),db:Session=Depends(get_db),user:User=Depends(mapper)):
    # The source side of one branch exactly as a run would compare it: joined, filtered, transformed, aggregated.
    try:rows=prepare(source_rows(db,user,payload,{}),payload)
    except HTTPException:raise
    except Exception:raise HTTPException(422,'Preview failed; verify the selected tables, joins, and provider permissions') from None
    items=[{k:v for k,v in row.items() if not k.startswith('__')} for row in rows[:limit]]
    return {'items':jsonable_encoder(items),'total':len(rows),'page':1,'page_size':limit}

def mapping_job(db,user,id):
    mapping=db.get(TableMapping,id)
    if not mapping or mapping.organization_id!=user.organization_id:raise HTTPException(404,'Mapping not found')
    job=db.scalar(select(ReconciliationJob).where(ReconciliationJob.mapping_id==id))
    if not job:raise HTTPException(422,'Mapping has no executable job')
    return mapping,job

@router.get('/mappings/{id}')
def mapping_detail(id:UUID,db:Session=Depends(get_db),user:User=Depends(current_user)):
    mapping,job=mapping_job(db,user,id)
    rules=db.scalars(select(ReconciliationRule).where(ReconciliationRule.job_id==job.id).order_by(ReconciliationRule.created_at,ReconciliationRule.id)).all()
    return {'id':mapping.id,'name':mapping.name,'mapping_type':mapping.mapping_type,'branches':[r.configuration for r in rules]}

@router.post('/reconciliation/run')
def run(payload:RunInput,db:Session=Depends(get_db),user:User=Depends(mapper)):
    mapping,job=mapping_job(db,user,payload.mapping_id)
    rules=db.scalars(select(ReconciliationRule).where(ReconciliationRule.job_id==job.id)).all()
    snapshot={'name':mapping.name,'mapping_type':mapping.mapping_type,'branches':[r.configuration for r in rules]}
    run=ReconciliationRun(organization_id=user.organization_id,job_id=job.id,executed_by=user.id,status='running',started_at=utcnow(),configuration_snapshot=snapshot)
    db.add(run);db.commit();started=time.monotonic()
    try:
        cache={};relevant=set();failed=set();results=[]
        for rule in rules:
            branch=Branch.model_validate(rule.configuration)
            source=source_rows(db,user,branch,cache)
            _,connection,resource=table_connection(db,user,branch.target_table_id)
            target=records(db,connection,resource,size=100,all_rows=True)
            stats,diffs,seen,bad=compare(source,target,branch);relevant|=seen;failed|=bad
            result=ReconciliationResult(run_id=run.id,edge_id=rule.edge_id,**jsonable_encoder(stats));db.add(result);db.flush();results.append(result)
            for diff in diffs:db.add(ReconciliationDifference(result_id=result.id,**jsonable_encoder(diff)))
            if time.monotonic()-started>90:raise HTTPException(422,'Execution exceeded 90 seconds; use a smaller staged dataset')
        overall={'source_records_compared':len(relevant),'source_records_matched_all_relevant_targets':len(relevant-failed),'source_records_with_differences':len(failed),'match_percentage':round(len(relevant-failed)/len(relevant)*100,2) if relevant else None}
        if results:results[0].rule_results=results[0].rule_results+[{'overall_source_coverage':overall}]
        run.status='succeeded';run.completed_at=utcnow();run.duration_ms=int((time.monotonic()-started)*1000)
        audit(db,'reconciliation.completed',user.id,'reconciliation',run.id,{'mapping_id':str(mapping.id)});db.commit()
        return {'run_id':run.id,'status':run.status,'overall':overall}
    except Exception as exc:
        db.rollback();run=db.get(ReconciliationRun,run.id);run.status='failed';run.completed_at=utcnow();run.duration_ms=int((time.monotonic()-started)*1000)
        run.error_details=str(exc.detail) if isinstance(exc,HTTPException) else 'Reconciliation failed; verify schema, mappings, and provider permissions'
        db.commit();raise HTTPException(422,run.error_details) from None

def owned_run(db,user,id):
    row=db.get(ReconciliationRun,id)
    if not row or row.organization_id!=user.organization_id:raise HTTPException(404,'Run not found')
    job=db.get(ReconciliationJob,row.job_id);mapping_job(db,user,job.mapping_id)
    return row

@router.get('/reconciliation/runs/{id}')
def run_detail(id:UUID,db:Session=Depends(get_db),user:User=Depends(current_user)):
    row=owned_run(db,user,id)
    return {f:getattr(row,f) for f in ['id','status','duration_ms','configuration_snapshot','error_details','started_at','completed_at']}

@router.get('/reconciliation/runs/{id}/results')
def results(id:UUID,db:Session=Depends(get_db),user:User=Depends(current_user)):
    owned_run(db,user,id)
    rows=db.scalars(select(ReconciliationResult).where(ReconciliationResult.run_id==id)).all()
    output=[];overall=None
    for row in rows:
        edge=db.get(MappingEdge,row.edge_id);table=db.get(RegisteredTable,edge.target_table_id)
        output.append({f:getattr(row,f) for f in ['id','source_count','target_count','matched','mismatched','missing','extra','duplicates','match_percentage','rule_results']}|{'target_table':table.table_name})
        for rule in row.rule_results:
            if 'overall_source_coverage' in rule:overall=rule['overall_source_coverage']
    return {'items':output,'overall':overall}

@router.get('/reconciliation/runs/{id}/differences')
def differences(id:UUID,page:int=Query(1,ge=1),page_size:int=Query(25,ge=1,le=100),db:Session=Depends(get_db),user:User=Depends(current_user)):
    owned_run(db,user,id)
    ids=select(ReconciliationResult.id).where(ReconciliationResult.run_id==id)
    query=select(ReconciliationDifference).where(ReconciliationDifference.result_id.in_(ids))
    from sqlalchemy import func
    total=db.scalar(select(func.count()).select_from(query.subquery()))
    return {'items':[{f:getattr(r,f) for f in ['id','result_id','business_key','difference_type','column_name','source_value','target_value']} for r in db.scalars(query.order_by(ReconciliationDifference.created_at,ReconciliationDifference.id).offset((page-1)*page_size).limit(page_size))],'total':total,'page':page,'page_size':page_size}

@router.get('/reconciliation/runs/{id}/export')
def export(id:UUID,db:Session=Depends(get_db),user:User=Depends(current_user)):
    import csv,io,json
    from sqlalchemy import func
    owned_run(db,user,id)
    ids=select(ReconciliationResult.id).where(ReconciliationResult.run_id==id)
    query=select(ReconciliationDifference).where(ReconciliationDifference.result_id.in_(ids))
    count=db.scalar(select(func.count()).select_from(query.subquery()))
    if count>50000:raise HTTPException(422,'Export exceeds 50,000 differences; narrow the mapping or filters')
    rows=db.scalars(query.order_by(ReconciliationDifference.id)).all()
    output=io.StringIO();writer=csv.writer(output)
    writer.writerow(['business_key','status','column','source_record','target_record'])
    def safe(value):
        value=str(value or '')
        return "'"+value if value.lstrip().startswith(('=','+','-','@')) else value
    for row in rows:
        writer.writerow([safe(json.dumps(jsonable_encoder(row.business_key))),row.difference_type,safe(row.column_name),safe(json.dumps(jsonable_encoder(row.source_value))),safe(json.dumps(jsonable_encoder(row.target_value)))])
    return StreamingResponse(iter([output.getvalue()]),media_type='text/csv',headers={'Content-Disposition':f'attachment; filename="reconciliation-{id}.csv"','Cache-Control':'no-store'})
