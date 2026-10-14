import hashlib
import hmac
import json
from uuid import UUID
from datetime import timedelta
from fastapi import APIRouter,Depends,HTTPException,Query,Request,UploadFile,File,Form
from fastapi.responses import RedirectResponse
from sqlalchemy import select,func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.core.security import current_user,encrypt_credential
from app.core.config import get_settings
from app.models import (User,ExternalApplication,IntegrationConnection,IntegrationAuthConfiguration,IntegrationResource,
    IntegrationSyncRun,IntegrationError,DatabaseConnection,AuditLog,RegisteredTable,TableMapping,ReconciliationRun)
from app.schemas.integrations import IntegrationInput,IngestInput
from app.connectors.integrations import (auth_row,save_secrets,read_secrets,application_slug,records,infer_schema,
    discover_postgres,authorize,complete_oauth,utcnow,request_json)
from app.connectors.network import validate_url,allowed_address
from app.services.integration_data import ensure_registered,replace_columns,ingest
from app.services.audit import audit

router=APIRouter(prefix='/api',tags=['Integrations'])

def writer(user:User=Depends(current_user)):
    if user.role not in ['admin','engineer']:raise HTTPException(403,'Engineer or administrator role required')
    return user

def mapper(user:User=Depends(current_user)):
    # Analysts configure mappings and run comparisons; they do not manage connections or destinations.
    if user.role not in ['admin','engineer','analyst']:raise HTTPException(403,'Analyst, engineer, or administrator role required')
    return user

def owned(db,user,id,active=True):
    row=db.get(IntegrationConnection,id)
    if not row or row.organization_id!=user.organization_id:raise HTTPException(404,'Integration not found')
    if active and row.status=='revoked':raise HTTPException(422,'Integration was revoked')
    return row

def visible_query(user):
    return select(IntegrationConnection).where(IntegrationConnection.organization_id==user.organization_id)

def view(db,row):
    application=db.get(ExternalApplication,row.application_id)
    return {f:getattr(row,f) for f in ['id','name','authentication_type','status','authorized_scopes','last_successful_sync','last_failed_sync','created_at','updated_at','configuration','database_connection_id']}|{'application':application.slug,'application_name':application.name}

def resource_view(row):
    return {f:getattr(row,f) for f in ['id','name','path','record_path','pagination','schema_snapshot','schema_version','available_filters','registered_table_id']}

def error(db,connection,user,operation,exc):
    db.rollback()
    row=db.get(IntegrationConnection,connection.id)
    message=str(exc.detail) if isinstance(exc,HTTPException) else 'Operation failed. Check provider permissions and configuration.'
    row.status='error'
    db.add(IntegrationError(connection_id=row.id,operation=operation,error_code=str(getattr(exc,'status_code',422)),message=message))
    audit(db,'integration.'+operation+'.failed',user.id,'integration',row.id)
    db.commit()
    raise HTTPException(getattr(exc,'status_code',422),message) from None

@router.get('/integrations/catalog')
def catalog(db:Session=Depends(get_db),user:User=Depends(current_user)):
    return [{'slug':r.slug,'name':r.name,'description':r.description,'authentication_methods':r.authentication_methods} for r in db.scalars(select(ExternalApplication).order_by(ExternalApplication.name))]

@router.get('/integrations')
def list_integrations(page:int=Query(1,ge=1),page_size:int=Query(25,ge=1,le=100),search:str=Query('',max_length=100),connected:bool=False,application:str=Query('',max_length=40),db:Session=Depends(get_db),user:User=Depends(current_user)):
    query=visible_query(user).where(IntegrationConnection.status!='revoked')
    if search:query=query.where(IntegrationConnection.name.icontains(search,autoescape=True))
    if connected:query=query.where(IntegrationConnection.status=='connected')
    if application:query=query.where(IntegrationConnection.application_id.in_(select(ExternalApplication.id).where(ExternalApplication.slug==application)))
    total=db.scalar(select(func.count()).select_from(query.subquery()))
    rows=db.scalars(query.order_by(IntegrationConnection.created_at.desc()).offset((page-1)*page_size).limit(page_size)).all()
    return {'items':[view(db,r) for r in rows],'total':total,'page':page,'page_size':page_size}

@router.post('/integrations',status_code=201)
def create(payload:IntegrationInput,db:Session=Depends(get_db),user:User=Depends(writer)):
    application=db.scalar(select(ExternalApplication).where(ExternalApplication.slug==payload.application))
    if not application:raise HTTPException(422,'Application is not installed')
    if payload.authentication_type not in application.authentication_methods:raise HTTPException(422,'Authentication method is not supported by this connector')
    if payload.application=='postgresql':allowed_address(payload.hostname,payload.port)
    else:
        validate_url(payload.base_url)
        if not payload.resources:raise HTTPException(422,'Declare at least one exposed API resource')
        if payload.authentication_type=='oauth2':
            for endpoint in [payload.authorization_url,payload.token_url,payload.revocation_url]:
                if endpoint:validate_url(endpoint)
    config=payload.model_dump(exclude={'secret','client_secret','webhook_secret','username','resources','name','application','authentication_type'})
    import uuid
    db_connection=DatabaseConnection(organization_id=user.organization_id,name=payload.name[:60]+' · '+str(uuid.uuid4()),connection_type=payload.application,
        classification=payload.classification,hostname=payload.hostname or None,port=payload.port if payload.application=='postgresql' else None,
        database_name=payload.database_name or None,schema_name=payload.schema_name,authentication_method=payload.authentication_type,owner_id=user.id)
    db.add(db_connection)
    try:
        db.flush()
        row=IntegrationConnection(organization_id=user.organization_id,application_id=application.id,owner_id=user.id,database_connection_id=db_connection.id,
            name=payload.name,authentication_type=payload.authentication_type,status='configured',configuration=config,authorized_scopes=[])
        db.add(row);db.flush()
        db_connection.credential_reference='integration:'+str(row.id)
        auth=IntegrationAuthConfiguration(connection_id=row.id)
        save_secrets(auth,{'secret':payload.secret,'username':payload.username,'client_secret':payload.client_secret,'webhook_secret':payload.webhook_secret})
        db.add(auth)
        for resource in payload.resources:
            item=resource.model_dump()
            if payload.application=='servicenow':
                item.update(path='/api/now/table/'+resource.name,record_path='result',pagination={'mode':'offset','size_parameter':'sysparm_limit','offset_parameter':'sysparm_offset','page_size':100},available_filters=['sysparm_query','sysparm_fields'])
            db.add(IntegrationResource(connection_id=row.id,**item,schema_snapshot=[],schema_version=0))
        audit(db,'integration.created',user.id,'integration',row.id)
        db.commit()
    except IntegrityError:
        db.rollback();raise HTTPException(409,'Connection or resource names must be unique') from None
    return view(db,row)

@router.get('/integrations/oauth/callback')
def callback(state:str=Query(...,min_length=20,max_length=256),code:str=Query(...,min_length=1,max_length=4096),db:Session=Depends(get_db),user:User=Depends(current_user)):
    row=complete_oauth(db,user,state,code)
    audit(db,'integration.authorized',user.id,'integration',row.id);db.commit()
    return RedirectResponse(get_settings().frontend_public_url.rstrip('/')+'/integrations?authorized='+str(row.id),status_code=303)

@router.post('/integrations/files/ingest')
async def file_ingestion(file:UploadFile=File(...),configuration:str=Form(...,max_length=10000),db:Session=Depends(get_db),user:User=Depends(writer)):
    import csv,io,re,uuid
    from pathlib import Path
    if not file.filename or not file.filename.lower().endswith('.csv'):raise HTTPException(422,'Only UTF-8 CSV files are supported by this file connector')
    content=await file.read(5_000_001)
    await file.close()
    if len(content)>5_000_000:raise HTTPException(413,'CSV upload exceeds 5 MB')
    try:
        data=csv.DictReader(io.StringIO(content.decode('utf-8-sig')))
        headers=data.fieldnames or []
        if not headers or len(headers)>100 or len(set(headers))!=len(headers) or any(not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]{0,62}',h) for h in headers):
            raise ValueError('CSV needs unique valid column names')
        rows=[]
        for row in data:
            if None in row or any(v is None for v in row.values()):raise ValueError('CSV rows do not match the header')
            rows.append(row)
            if len(rows)>get_settings().integration_max_rows:raise ValueError('CSV exceeds the configured row limit')
        if not rows:raise ValueError('CSV has no records')
        config=json.loads(configuration)
        if not isinstance(config,dict):raise ValueError('Configuration must be an object')
    except (ValueError,UnicodeError,csv.Error) as exc:
        raise HTTPException(422,'Invalid CSV or ingestion configuration') from None
    # Validate the complete destination configuration before creating a storage object.
    identifier=uuid.uuid4();resource_id=uuid.uuid4()
    try:payload=IngestInput.model_validate({**config,'resource_id':str(resource_id)})
    except ValueError:raise HTTPException(422,'Invalid ingestion configuration') from None
    if not payload.confirmed:raise HTTPException(422,'Confirm destination changes before ingestion')
    if not set(payload.columns).issubset(headers):raise HTTPException(422,'Selected columns are absent from the CSV header')
    destination=owned(db,user,payload.destination_id)
    if application_slug(db,destination)!='postgresql':raise HTTPException(422,'Choose an authorized PostgreSQL destination')
    application=db.scalar(select(ExternalApplication).where(ExternalApplication.slug=='csv'))
    if not application:raise HTTPException(503,'CSV connector migration is not applied')
    db_connection=DatabaseConnection(organization_id=user.organization_id,name='CSV upload '+str(identifier),connection_type='csv',classification='source',authentication_method='file',owner_id=user.id)
    db.add(db_connection);db.flush()
    source=IntegrationConnection(id=identifier,organization_id=user.organization_id,application_id=application.id,owner_id=user.id,database_connection_id=db_connection.id,name='CSV upload '+str(identifier)[:8],authentication_type='file',status='connected',configuration={'storage_key':str(identifier),'classification':'source'},authorized_scopes=[])
    db.add(source);db.flush()
    db.add(IntegrationAuthConfiguration(connection_id=source.id))
    resource=IntegrationResource(id=resource_id,connection_id=source.id,name='uploaded_'+str(identifier).replace('-','')[:12],path='/file',record_path='',pagination={},schema_snapshot=infer_schema(rows),schema_version=1,available_filters=[])
    db.add(resource)
    storage=Path(get_settings().dataset_storage);storage.mkdir(parents=True,exist_ok=True)
    stored=storage/(str(identifier)+'.enc')
    stored.write_text(encrypt_credential(json.dumps(rows)),encoding='utf-8')
    try:
        audit(db,'integration.file_uploaded',user.id,'integration',source.id,{'rows':len(rows)})
        db.commit()
        return ingest(db,source,resource,destination,payload,user)
    except Exception:
        # A failed import must not leave a stored file or a usable source behind; the failed run stays as history.
        stored.unlink(missing_ok=True)
        db.rollback()
        failed=db.get(IntegrationConnection,identifier)
        if failed:failed.status='revoked';db.commit()
        raise

@router.get('/integrations/{id}')
def detail(id:UUID,db:Session=Depends(get_db),user:User=Depends(current_user)):
    row=owned(db,user,id,False)
    errors=db.scalars(select(IntegrationError).where(IntegrationError.connection_id==id).order_by(IntegrationError.created_at.desc()).limit(10)).all()
    return view(db,row)|{'errors':[{'operation':r.operation,'message':r.message,'created_at':r.created_at} for r in errors]}

@router.post('/integrations/{id}/authorize')
def start_authorize(id:UUID,db:Session=Depends(get_db),user:User=Depends(writer)):
    row=owned(db,user,id)
    audit(db,'integration.authorization_started',user.id,'integration',id)
    return {'authorization_url':authorize(db,row)}

@router.post('/integrations/{id}/test')
def test_connection(id:UUID,db:Session=Depends(get_db),user:User=Depends(writer)):
    row=owned(db,user,id)
    try:
        if application_slug(db,row)=='postgresql':
            discovered=discover_postgres(db,row)
            for info in discovered:
                resource=db.scalar(select(IntegrationResource).where(IntegrationResource.connection_id==id,IntegrationResource.name==info['name']))
                if not resource:
                    resource=IntegrationResource(connection_id=id,name=info['name'],path=info['path'],record_path='',pagination={},schema_snapshot=info['columns'],schema_version=1,available_filters=[])
                    db.add(resource);db.flush()
                elif resource.schema_snapshot!=info['columns']:
                    resource.schema_snapshot=info['columns'];resource.schema_version+=1
                ensure_registered(db,row,resource)
        else:
            first=db.scalar(select(IntegrationResource).where(IntegrationResource.connection_id==id).order_by(IntegrationResource.created_at).limit(1))
            if not first:raise HTTPException(422,'No exposed resources are configured')
            records(db,row,first,size=1)
        row.status='connected';audit(db,'integration.tested',user.id,'integration',id);db.commit()
        return {'status':'connected','message':'Authorized connection validated'}
    except Exception as exc:return error(db,row,user,'test',exc)

@router.get('/integrations/{id}/resources')
def resources(id:UUID,page:int=Query(1,ge=1),page_size:int=Query(100,ge=1,le=200),db:Session=Depends(get_db),user:User=Depends(current_user)):
    owned(db,user,id)
    query=select(IntegrationResource).where(IntegrationResource.connection_id==id)
    total=db.scalar(select(func.count()).select_from(query.subquery()))
    return {'items':[resource_view(r) for r in db.scalars(query.order_by(IntegrationResource.name).offset((page-1)*page_size).limit(page_size))],'total':total,'page':page,'page_size':page_size}

def selected_resource(db,id,resource_id):
    resource=db.get(IntegrationResource,resource_id)
    if not resource or resource.connection_id!=id:raise HTTPException(404,'Resource not found for this integration')
    return resource

@router.get('/integrations/{id}/schema')
def schema(id:UUID,resource_id:UUID,db:Session=Depends(get_db),user:User=Depends(current_user)):
    owned(db,user,id)
    resource=selected_resource(db,id,resource_id)
    return {'fields':resource.schema_snapshot,'version':resource.schema_version,'available_filters':resource.available_filters}

@router.get('/integrations/{id}/preview')
def preview(id:UUID,resource_id:UUID,page:int=Query(1,ge=1,le=1000),page_size:int=Query(25,ge=1,le=100),filters:str=Query('{}',max_length=2000),db:Session=Depends(get_db),user:User=Depends(current_user)):
    row=owned(db,user,id);resource=selected_resource(db,id,resource_id)
    try:
        parsed=json.loads(filters)
        if not isinstance(parsed,dict) or len(parsed)>20:raise ValueError()
    except ValueError:raise HTTPException(422,'Filters must be a small JSON object') from None
    slug=application_slug(db,row);mode=resource.pagination.get('mode')
    # Reject invalid request input here so it is not recorded as a connection failure.
    if any(key not in resource.available_filters for key in parsed):raise HTTPException(422,'Filter is not declared for this resource')
    if slug not in ['postgresql','csv'] and mode=='cursor' and page!=1:raise HTTPException(422,'Cursor resources support first-page preview or full ingestion')
    try:
        items=records(db,row,resource,page,page_size,filters=parsed)
        fields=infer_schema(items) if slug!='postgresql' else resource.schema_snapshot
        changed=bool(resource.schema_snapshot and fields and fields!=resource.schema_snapshot)
        return {'items':items,'fields':fields,'schema_changed':changed,'schema_version':resource.schema_version,'page':page,'page_size':page_size,
            'has_more':len(items)==page_size and (slug in ['postgresql','csv'] or mode in ['offset','page'])}
    except Exception as exc:return error(db,row,user,'preview',exc)

@router.post('/integrations/{id}/resources/{resource_id}/approve-schema')
def approve_schema(id:UUID,resource_id:UUID,db:Session=Depends(get_db),user:User=Depends(writer)):
    row=owned(db,user,id);resource=selected_resource(db,id,resource_id)
    items=records(db,row,resource,size=100)
    fields=infer_schema(items) if application_slug(db,row)!='postgresql' else resource.schema_snapshot
    if not fields:raise HTTPException(422,'No schema can be inferred from an empty resource')
    resource.schema_snapshot=fields;resource.schema_version+=1;ensure_registered(db,row,resource)
    replace_columns(db,db.get(RegisteredTable,resource.registered_table_id),fields)
    audit(db,'integration.schema_approved',user.id,'integration',id);db.commit()
    return resource_view(resource)

@router.post('/integrations/{id}/ingest')
def run_ingestion(id:UUID,payload:IngestInput,db:Session=Depends(get_db),user:User=Depends(writer)):
    source=owned(db,user,id);destination=owned(db,user,payload.destination_id)
    if application_slug(db,destination)!='postgresql':raise HTTPException(422,'The initial ingestion destination must be PostgreSQL')
    resource=selected_resource(db,id,payload.resource_id)
    return ingest(db,source,resource,destination,payload,user)

@router.get('/integrations/{id}/sync-history')
def history(id:UUID,page:int=Query(1,ge=1),page_size:int=Query(25,ge=1,le=100),db:Session=Depends(get_db),user:User=Depends(current_user)):
    owned(db,user,id,False)
    query=select(IntegrationSyncRun).where(IntegrationSyncRun.connection_id==id)
    total=db.scalar(select(func.count()).select_from(query.subquery()))
    return {'items':[{f:getattr(r,f) for f in ['id','configuration_id','status','record_count','started_at','completed_at','error_details']} for r in db.scalars(query.order_by(IntegrationSyncRun.started_at.desc()).offset((page-1)*page_size).limit(page_size))],'total':total,'page':page,'page_size':page_size}

@router.delete('/integrations/{id}')
def revoke(id:UUID,db:Session=Depends(get_db),user:User=Depends(writer)):
    row=owned(db,user,id,False);auth=auth_row(db,row,True);creds=read_secrets(auth)
    remote='not_configured'
    if row.authentication_type=='oauth2' and row.configuration.get('revocation_url') and creds.get('access_token'):
        payload={'token':creds.get('refresh_token') or creds['access_token'],'client_id':row.configuration['client_id']}
        if creds.get('client_secret'):payload['client_secret']=creds['client_secret']
        try:request_json('POST',row.configuration['revocation_url'],data=payload,retry=False,allow_empty=True);remote='requested'
        except HTTPException:remote='failed'
    auth.encrypted_secrets=None;auth.state_hash=None;auth.state_expires_at=None;auth.token_expires_at=None
    row.status='revoked';audit(db,'integration.revoked',user.id,'integration',id,{'remote_revocation':remote});db.commit()
    return {'status':'revoked','remote_revocation':remote,'message':'Local credentials removed. Revoke provider access in the provider portal if remote revocation was not confirmed.'}

@router.post('/integrations/{id}/webhook')
async def webhook(id:UUID,request:Request,db:Session=Depends(get_db)):
    row=db.get(IntegrationConnection,id)
    if not row or row.status=='revoked':raise HTTPException(404,'Integration not found')
    secret=read_secrets(auth_row(db,row)).get('webhook_secret')
    if not secret:raise HTTPException(403,'Webhook is not enabled')
    timestamp=request.headers.get('X-Reconcile-Timestamp','')
    try:
        if abs(utcnow().timestamp()-int(timestamp))>300:raise ValueError()
    except ValueError:raise HTTPException(403,'Webhook timestamp is invalid') from None
    body=bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body)>65536:raise HTTPException(413,'Webhook body is too large')
    signature=hmac.new(secret.encode(),timestamp.encode()+b'.'+body,hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature,request.headers.get('X-Reconcile-Signature','')):raise HTTPException(403,'Webhook signature is invalid')
    event=hashlib.sha256(timestamp.encode()+body).hexdigest()
    # Serialize deduplication per integration, retain only event identity (not payload).
    db.execute(select(IntegrationConnection).where(IntegrationConnection.id==id).with_for_update())
    prior=db.scalar(select(AuditLog.id).where(AuditLog.action=='integration.webhook',AuditLog.resource_id==str(id),AuditLog.details['event'].astext==event))
    if prior:return {'status':'duplicate'}
    audit(db,'integration.webhook',None,'integration',id,{'event':event},organization_id=row.organization_id);db.commit()
    return {'status':'accepted','message':'Signed event recorded; automatic sync execution is not enabled'}

@router.get('/search')
def global_search(q:str=Query('',max_length=100),db:Session=Depends(get_db),user:User=Depends(current_user)):
    if not q.strip():return []
    items=[{'label':'Integration · '+r.name,'href':'/integrations?connection='+str(r.id)} for r in db.scalars(visible_query(user).where(IntegrationConnection.name.icontains(q,autoescape=True),IntegrationConnection.status!='revoked').limit(10))]
    for model,field,path in [(RegisteredTable,RegisteredTable.table_name,'tables'),(TableMapping,TableMapping.name,'mapping')]:
        items.extend({'label':str(getattr(r,field.key)),'href':'/'+path} for r in db.scalars(select(model).where(model.organization_id==user.organization_id,field.icontains(q,autoescape=True)).limit(10)))
    return items

@router.get('/notifications')
def notifications(db:Session=Depends(get_db),user:User=Depends(current_user)):
    query=select(AuditLog).where(AuditLog.actor_id==user.id,AuditLog.organization_id==user.organization_id).order_by(AuditLog.created_at.desc()).limit(10)
    return [{'action':r.action,'created_at':r.created_at} for r in db.scalars(query)]
