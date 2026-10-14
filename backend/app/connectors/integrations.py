import base64
import json
import hashlib
import secrets
import time
from contextlib import contextmanager
from datetime import datetime,timedelta,timezone
from urllib.parse import urlencode
from fastapi import HTTPException
from sqlalchemy import create_engine,inspect,select,MetaData,Table,func
from sqlalchemy.engine import URL,make_url
from app.core.config import get_settings
from app.core.security import encrypt_credential,decrypt_credential
from app.models import IntegrationAuthConfiguration,IntegrationConnection,IntegrationResource,ExternalApplication
from app.connectors.network import validate_url,allowed_address,request_json

def utcnow():return datetime.now(timezone.utc)

def auth_row(db,connection,lock=False):
    query=select(IntegrationAuthConfiguration).where(IntegrationAuthConfiguration.connection_id==connection.id)
    if lock:query=query.with_for_update()
    return db.scalar(query)

def read_secrets(auth):
    if not auth or not auth.encrypted_secrets:return {}
    try:return json.loads(decrypt_credential(auth.encrypted_secrets))
    except Exception:raise HTTPException(503,'Integration credentials cannot be decrypted; check the server encryption key') from None

def save_secrets(auth,values):auth.encrypted_secrets=encrypt_credential(json.dumps(values))

def application_slug(db,connection):return db.get(ExternalApplication,connection.application_id).slug

def token_headers(db,connection):
    auth=auth_row(db,connection,lock=True)
    creds=read_secrets(auth)
    method=connection.authentication_type
    if method=='oauth2':
        if not creds.get('access_token'):raise HTTPException(422,'Authorize this integration first')
        if auth.token_expires_at and auth.token_expires_at<=utcnow()+timedelta(seconds=30):
            if not creds.get('refresh_token'):raise HTTPException(422,'Authorization has expired. Reauthorize this connection.')
            payload={'grant_type':'refresh_token','refresh_token':creds['refresh_token'],'client_id':connection.configuration['client_id']}
            if creds.get('client_secret'):payload['client_secret']=creds['client_secret']
            token=request_json('POST',connection.configuration['token_url'],data=payload,retry=False)
            accept_tokens(connection,auth,creds,token)
            db.commit()
        return {'Authorization':'Bearer '+creds['access_token']}
    if method=='bearer':return {'Authorization':'Bearer '+creds['secret']}
    if method=='api_key':return {connection.configuration['api_key_header']:creds['secret']}
    if method=='basic':
        value=base64.b64encode((creds['username']+':'+creds['secret']).encode()).decode()
        return {'Authorization':'Basic '+value}
    return {}

def accept_tokens(connection,auth,creds,token):
    if not isinstance(token,dict) or not token.get('access_token') or str(token.get('token_type','bearer')).lower()!='bearer':
        raise HTTPException(422,'Provider did not issue a supported bearer token')
    requested=set(connection.configuration.get('scopes',[]))
    granted=set(str(token.get('scope',' '.join(requested))).split())
    if not requested.issubset(granted):raise HTTPException(422,'Provider did not grant all required scopes')
    expires=token.get('expires_in')
    if expires is not None:
        try:expires=int(expires)
        except (ValueError,TypeError):raise HTTPException(422,'Provider returned invalid token expiry') from None
        if not 0<expires<=31_536_000:raise HTTPException(422,'Provider returned unsupported token expiry')
    creds['access_token']=str(token['access_token'])
    if token.get('refresh_token'):creds['refresh_token']=str(token['refresh_token'])
    auth.token_expires_at=utcnow()+timedelta(seconds=expires) if expires else None
    connection.authorized_scopes=sorted(granted)
    save_secrets(auth,creds)

def authorize(db,connection):
    if connection.authentication_type!='oauth2':raise HTTPException(422,'This connection does not use OAuth')
    config=connection.configuration
    validate_url(config['authorization_url']);validate_url(config['token_url'])
    auth=auth_row(db,connection,lock=True)
    state=secrets.token_urlsafe(32);verifier=secrets.token_urlsafe(64)
    challenge=base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b'=').decode()
    auth.state_hash=hashlib.sha256(state.encode()).hexdigest();auth.state_expires_at=utcnow()+timedelta(minutes=10)
    creds=read_secrets(auth);creds['pkce_verifier']=verifier;save_secrets(auth,creds)
    origin=get_settings().frontend_public_url.rstrip('/')
    if origin not in get_settings().origins:raise HTTPException(503,'OAuth callback origin must be configured in ALLOWED_ORIGINS')
    redirect=origin+'/api/integrations/oauth/callback'
    db.commit()
    return config['authorization_url']+'?'+urlencode({'response_type':'code','client_id':config['client_id'],
        'redirect_uri':redirect,'scope':' '.join(config.get('scopes',[])),'state':state,
        'code_challenge':challenge,'code_challenge_method':'S256'})

def complete_oauth(db,user,state,code):
    state_hash=hashlib.sha256(state.encode()).hexdigest()
    auth=db.scalar(select(IntegrationAuthConfiguration).where(IntegrationAuthConfiguration.state_hash==state_hash).with_for_update())
    if not auth or not auth.state_expires_at or auth.state_expires_at<utcnow():raise HTTPException(400,'Authorization state is invalid or expired')
    connection=db.get(IntegrationConnection,auth.connection_id)
    if connection.organization_id!=user.organization_id or connection.status=='revoked':raise HTTPException(403,'Authorization does not belong to this organization')
    creds=read_secrets(auth);verifier=creds.pop('pkce_verifier',None)
    if not verifier:raise HTTPException(400,'Authorization verifier is missing')
    # Consume state before exchange. A failed exchange requires a fresh authorization.
    auth.state_hash=None;auth.state_expires_at=None;save_secrets(auth,creds);db.commit()
    payload={'grant_type':'authorization_code','code':code,'client_id':connection.configuration['client_id'],
             'redirect_uri':get_settings().frontend_public_url.rstrip('/')+'/api/integrations/oauth/callback','code_verifier':verifier}
    if creds.get('client_secret'):payload['client_secret']=creds['client_secret']
    token=request_json('POST',connection.configuration['token_url'],data=payload,retry=False)
    accept_tokens(connection,auth,creds,token)
    connection.status='connected';db.commit()
    return connection

@contextmanager
def postgres_engine(db,connection):
    if application_slug(db,connection)!='postgresql':raise HTTPException(422,'A PostgreSQL connection is required')
    config=connection.configuration;creds=read_secrets(auth_row(db,connection))
    address=allowed_address(config['hostname'],config['port'])
    # Never write customer datasets into the metadata database.
    if config['database_name']==make_url(get_settings().database_url).database:
        raise HTTPException(422,'Use a separate data database, not the application metadata database')
    url=URL.create('postgresql+psycopg',username=creds['username'],password=creds['secret'],host=config['hostname'],
                   port=config['port'],database=config['database_name'])
    engine=create_engine(url,hide_parameters=True,connect_args={'hostaddr':address,'sslmode':config['sslmode'],
        'connect_timeout':5,'options':'-c statement_timeout=15000 -c lock_timeout=3000'},pool_pre_ping=True)
    try:yield engine
    except HTTPException:raise
    except Exception:raise HTTPException(422,'PostgreSQL operation failed. Check TLS, credentials, schema, and database permissions.') from None
    finally:engine.dispose()

def dotted(value,path):
    for name in path.split('.') if path else []:
        if not isinstance(value,dict) or name not in value:raise HTTPException(422,'Configured response path was not found')
        value=value[name]
    return value

def rest_records(db,connection,resource,page=1,size=None,all_rows=False,filters=None):
    params={}
    for key,value in (filters or {}).items():
        if key not in resource.available_filters:raise HTTPException(422,'Filter is not declared for this resource')
        params[key]=str(value)
    pagination=resource.pagination;mode=pagination.get('mode','none');page_size=size or pagination.get('page_size',100)
    limit=get_settings().integration_max_rows
    headers=token_headers(db,connection)
    url=connection.configuration['base_url'].rstrip('/')+resource.path
    rows=[];cursor=None;seen=set();index=page-1;total_bytes=0
    if mode=='cursor' and page!=1:raise HTTPException(422,'Cursor resources support first-page preview or full ingestion')
    deadline=time.monotonic()+60
    for iteration in range(101):
        if time.monotonic()>deadline:raise HTTPException(422,'Resource read exceeded 60 seconds; use a smaller staged dataset')
        call_params=dict(params)
        if mode!='none':call_params[pagination.get('size_parameter','limit')]=page_size
        if mode in ['offset','page']:call_params[pagination.get('offset_parameter','offset')]=index*page_size if mode=='offset' else index+1
        if mode=='cursor' and cursor is not None:call_params[pagination.get('offset_parameter','cursor')]=cursor
        body=request_json('GET',url,headers=headers,params=call_params)
        records=dotted(body,resource.record_path)
        if not isinstance(records,list) or any(not isinstance(row,dict) for row in records):raise HTTPException(422,'Resource must expose a JSON array of objects')
        total_bytes+=len(json.dumps(records).encode())
        if total_bytes>25_000_000:raise HTTPException(422,'Dataset exceeds the 25 MB execution limit; stage a smaller dataset')
        if len(records)>page_size and mode!='none':raise HTTPException(422,'Provider ignored the configured page size')
        rows.extend(records)
        if len(rows)>limit:raise HTTPException(422,f'Dataset exceeds the {limit:,}-row execution limit. Stage a bounded dataset first.')
        if not all_rows:return rows[:page_size]
        if mode=='none':return rows
        if mode=='cursor':
            cursor=dotted(body,pagination.get('next_cursor_path','next_cursor'))
            if not cursor:return rows
            key=str(cursor)
            if key in seen:raise HTTPException(422,'Provider repeated a pagination cursor')
            seen.add(key)
        elif len(records)<page_size:return rows
        index+=1
    raise HTTPException(422,'Provider pagination exceeded 100 pages; nothing was ingested')

def records(db,connection,resource,page=1,size=25,all_rows=False,filters=None):
    if connection.status=='revoked':raise HTTPException(422,'Integration was revoked')
    if application_slug(db,connection)=='csv':
        from pathlib import Path
        import uuid
        storage_key=str(uuid.UUID(connection.configuration['storage_key']))
        data=json.loads(decrypt_credential((Path(get_settings().dataset_storage)/(storage_key+'.enc')).read_text(encoding='utf-8')))
        return data if all_rows else data[(page-1)*size:page*size]
    if application_slug(db,connection)!='postgresql':return rest_records(db,connection,resource,page,size,all_rows,filters)
    if filters:raise HTTPException(422,'PostgreSQL preview filters are not configured for this resource')
    schema,name=resource.path.split('.',1)
    with postgres_engine(db,connection) as engine,engine.connect() as conn:
        table=Table(name,MetaData(),schema=schema,autoload_with=conn)
        limit=get_settings().integration_max_rows if all_rows else size
        order=list(table.primary_key.columns)
        if not order:order=[table.c[k] for k in table.c.keys()]
        query=select(table).order_by(*order).offset(0 if all_rows else (page-1)*size).limit(limit+1 if all_rows else limit)
        rows=[dict(r) for r in conn.execute(query).mappings()]
        if all_rows and len(rows)>limit:raise HTTPException(422,f'Dataset exceeds the {limit:,}-row execution limit')
        return rows

def infer_schema(rows):
    names=sorted({key for row in rows for key in row})
    output=[]
    for name in names:
        values=[row.get(name) for row in rows if row.get(name) is not None]
        types=set()
        for value in values:
            if isinstance(value,bool):types.add('BOOLEAN')
            elif isinstance(value,int):types.add('BIGINT')
            elif isinstance(value,float):types.add('DECIMAL')
            else:types.add('TEXT')
        dtype=next(iter(types)) if len(types)==1 else 'DECIMAL' if types and types<={'BIGINT','DECIMAL'} else 'TEXT'
        output.append({'name':name,'data_type':dtype,'nullable':any(row.get(name) is None for row in rows)})
    return output

def discover_postgres(db,connection):
    schema=connection.configuration['schema_name']
    with postgres_engine(db,connection) as engine:
        inspector=inspect(engine)
        if schema not in inspector.get_schema_names():raise HTTPException(422,'Configured schema is not accessible')
        output=[]
        for name in inspector.get_table_names(schema=schema)[:200]:
            if name.startswith('_reconcile_'):continue
            qualified=engine.dialect.identifier_preparer.quote_schema(schema)+'.'+engine.dialect.identifier_preparer.quote(name)
            with engine.connect() as conn:
                if not conn.scalar(select(func.has_table_privilege(qualified,'SELECT'))):continue
            columns=inspector.get_columns(name,schema=schema)
            output.append({'name':name,'path':schema+'.'+name,'columns':[{'name':c['name'],'data_type':str(c['type']),'nullable':c['nullable']} for c in columns]})
        return output
