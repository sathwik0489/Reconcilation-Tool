import json
import hmac
import hashlib
from contextlib import contextmanager
from datetime import datetime,timezone
from urllib.parse import urlsplit,parse_qs
import pytest
from fastapi import HTTPException
from sqlalchemy import select,Table,MetaData,Column,BigInteger,Text,Numeric,insert
from app.models import (User,IntegrationConnection,IntegrationAuthConfiguration,IntegrationResource,IntegrationSyncRun,IntegrationError,
    ExternalApplication,DatabaseConnection,RegisteredTable,TableColumn,AuditLog)
from app.core.security import password_hasher,decrypt_credential
from app.core.config import get_settings
from app.connectors import network
from app.connectors import integrations as adapters
from app.services import integration_data
from app.schemas.reconciliation import Branch
from app.reconciliation.engine import compare,joined
from tests.conftest import make_user,sign_in

@pytest.fixture
def authorized_hosts(monkeypatch):
    monkeypatch.setattr('app.api.integrations.validate_url',lambda url:(None,None))
    monkeypatch.setattr('app.api.integrations.allowed_address',lambda h,p:'203.0.113.5')
    monkeypatch.setattr(adapters,'validate_url',lambda url:(None,None))

def payload(name='CRM',application='rest',**extra):
    return {'name':name,'application':application,'base_url':'https://api.example.com','authentication_type':'bearer','secret':'private-bearer-token',
        'resources':[{'name':'accounts','path':'/accounts','record_path':'data','pagination':{'mode':'offset','size_parameter':'limit','offset_parameter':'offset','page_size':100}}],**extra}

def create(client,**kwargs):
    response=client.post('/api/integrations',json=payload(**kwargs))
    assert response.status_code==201,response.text
    return response.json()

def test_catalog_and_authenticated_routes(signed_in):
    items=signed_in.get('/api/integrations/catalog').json()
    assert {'rest','postgresql','servicenow'}<={r['slug'] for r in items}
    assert signed_in.get('/api/integrations').json()['total']==0

def test_long_valid_names_do_not_overflow_metadata(signed_in,authorized_hosts):
    first=create(signed_in,name='A'*100)
    second=create(signed_in,name='A'*99+'B')
    assert len(first['name'])==100 and first['id']!=second['id']

def test_credentials_encrypted_and_redacted(signed_in,db,authorized_hosts):
    row=create(signed_in)
    auth=db.scalar(select(IntegrationAuthConfiguration).where(IntegrationAuthConfiguration.connection_id==row['id']))
    assert 'private-bearer-token' not in auth.encrypted_secrets
    assert 'private-bearer-token' in decrypt_credential(auth.encrypted_secrets)
    for path in ['/api/integrations','/api/integrations/'+row['id'],'/api/connections']:
        data=signed_in.get(path).text
        assert 'private-bearer-token' not in data and 'encrypted_secrets' not in data

def test_validation_does_not_echo_rejected_password_or_token(signed_in,authorized_hosts):
    secret='super-private-token-'*300
    response=signed_in.post('/api/integrations',json=payload(secret=secret))
    assert response.status_code==422
    assert 'super-private-token-' not in response.text
    response=signed_in.post('/api/auth/login',json={'email':'admin@example.com','password':'private-password-'*20})
    assert response.status_code==422 and 'private-password-' not in response.text

def test_organization_isolation_and_viewer_write_denial(signed_in,db,admin,authorized_hosts):
    row=create(signed_in)
    # A colleague in the same organization can see the connection but, as a viewer, cannot change anything.
    colleague=make_user(db,'colleague@example.com','viewer','Other-password-1234',organization=admin.organization_id)
    sign_in(signed_in,colleague,'Other-password-1234')
    assert signed_in.get('/api/integrations/'+row['id']).status_code==200
    assert signed_in.get('/api/integrations').json()['total']==1
    assert signed_in.post('/api/integrations',json=payload()).status_code==403
    assert signed_in.delete('/api/integrations/'+row['id']).status_code==403
    # Even an administrator of another organization cannot see or reach it.
    outsider=make_user(db,'other@example.com','admin','Other-password-1234',organization_name='Another organization')
    sign_in(signed_in,outsider,'Other-password-1234')
    assert signed_in.get('/api/integrations/'+row['id']).status_code==404
    assert signed_in.get('/api/integrations').json()['total']==0
    assert signed_in.post('/api/integrations/'+row['id']+'/test').status_code==404
    assert signed_in.delete('/api/integrations/'+row['id']).status_code==404

def test_host_allowlists_and_private_addresses(monkeypatch):
    settings=get_settings()
    monkeypatch.setattr(settings,'integration_allowed_hosts','api.example.com')
    monkeypatch.setattr(settings,'integration_private_hosts','')
    monkeypatch.setattr(network.socket,'getaddrinfo',lambda *a,**k:[(2,1,6,'',('127.0.0.1',443))])
    with pytest.raises(HTTPException):network.validate_url('https://api.example.com/accounts')
    with pytest.raises(HTTPException):network.validate_url('https://unauthorized.example.com/accounts')
    with pytest.raises(HTTPException):network.validate_url('http://api.example.com/accounts')
    with pytest.raises(HTTPException):network.validate_url('https://user:password@api.example.com/accounts')
    monkeypatch.setattr(settings,'integration_private_hosts','api.example.com')
    assert network.allowed_address('api.example.com',443)=='127.0.0.1'

def test_resource_validation_and_no_provider_password_login(signed_in,authorized_hosts):
    bad=payload();bad['resources'][0]['path']='//evil.example.com/accounts'
    assert signed_in.post('/api/integrations',json=bad).status_code==422
    bad=payload(application='servicenow',authentication_type='basic',username='someone')
    assert signed_in.post('/api/integrations',json=bad).status_code==422

def test_resource_preview_schema_approval(signed_in,db,authorized_hosts,monkeypatch):
    row=create(signed_in)
    monkeypatch.setattr(adapters,'request_json',lambda *a,**k:{'data':[{'id':1,'name':'Alice','amount':12.5}]})
    resources=signed_in.get('/api/integrations/'+row['id']+'/resources').json()['items']
    resource=resources[0]
    response=signed_in.get(f"/api/integrations/{row['id']}/preview?resource_id={resource['id']}")
    assert response.status_code==200,response.text
    assert response.json()['fields'][0]['data_type']=='DECIMAL'
    assert signed_in.get(f"/api/integrations/{row['id']}/preview?resource_id={resource['id']}&filters=%7B%22unauthorized%22%3A1%7D").status_code==422
    approved=signed_in.post(f"/api/integrations/{row['id']}/resources/{resource['id']}/approve-schema")
    assert approved.status_code==200,approved.text
    assert approved.json()['registered_table_id']
    assert db.scalar(select(RegisteredTable.id))

def test_resource_belongs_to_integration(signed_in,authorized_hosts):
    first=create(signed_in);second=create(signed_in,name='Other')
    resource=signed_in.get('/api/integrations/'+first['id']+'/resources').json()['items'][0]
    assert signed_in.get(f"/api/integrations/{second['id']}/preview?resource_id={resource['id']}").status_code==404

def test_oauth_pkce_state_and_server_side_exchange(signed_in,db,authorized_hosts,monkeypatch):
    row=create(signed_in,authentication_type='oauth2',client_id='client',client_secret='client-secret',authorization_url='https://login.example.com/authorize',token_url='https://login.example.com/token',scopes=['read'])
    response=signed_in.post('/api/integrations/'+row['id']+'/authorize')
    assert response.status_code==200,response.text
    params=parse_qs(urlsplit(response.json()['authorization_url']).query)
    assert params['code_challenge_method']==['S256']
    assert 'client-secret' not in response.text and 'code_verifier' not in response.text
    captured={}
    def token(*args,**kwargs):
        captured.update(kwargs['data']);return {'access_token':'issued-token','refresh_token':'refresh-token','token_type':'Bearer','scope':'read','expires_in':3600}
    monkeypatch.setattr(adapters,'request_json',token)
    state=params['state'][0]
    callback=signed_in.get('/api/integrations/oauth/callback',params={'state':state,'code':'provider-code'},follow_redirects=False)
    assert callback.status_code==303,callback.text
    assert captured['code_verifier']
    assert signed_in.get('/api/integrations/oauth/callback',params={'state':state,'code':'replay'},follow_redirects=False).status_code==400
    assert 'issued-token' not in signed_in.get('/api/integrations/'+row['id']).text

def test_refresh_and_scope_validation(signed_in,db,authorized_hosts,monkeypatch):
    row=create(signed_in,authentication_type='oauth2',client_id='client',authorization_url='https://login.example.com/authorize',token_url='https://login.example.com/token',scopes=['read'])
    connection=db.get(IntegrationConnection,row['id']);auth=adapters.auth_row(db,connection)
    creds=adapters.read_secrets(auth);creds.update(access_token='expired',refresh_token='refresh');adapters.save_secrets(auth,creds)
    auth.token_expires_at=datetime(2020,1,1,tzinfo=timezone.utc);db.commit()
    monkeypatch.setattr(adapters,'request_json',lambda *a,**k:{'access_token':'renewed','scope':'read','expires_in':3600})
    assert adapters.token_headers(db,connection)['Authorization']=='Bearer renewed'
    with pytest.raises(HTTPException):adapters.accept_tokens(connection,auth,creds,{'access_token':'bad','scope':'other'})

def test_paginated_fetch_and_row_limit(signed_in,db,authorized_hosts,monkeypatch):
    row=create(signed_in);connection=db.get(IntegrationConnection,row['id']);resource=db.scalar(select(IntegrationResource).where(IntegrationResource.connection_id==row['id']))
    seen=[]
    def pages(*a,**k):
        offset=k['params']['offset'];seen.append(offset)
        return {'data':[{'id':offset},{'id':offset+1}]} if offset<4 else {'data':[]}
    monkeypatch.setattr(adapters,'request_json',pages)
    assert len(adapters.rest_records(db,connection,resource,size=2,all_rows=True))==4
    assert seen==[0,2,4]
    monkeypatch.setattr(get_settings(),'integration_max_rows',3)
    with pytest.raises(HTTPException):adapters.rest_records(db,connection,resource,size=2,all_rows=True)

def test_service_now_official_adapter_parameters(signed_in,authorized_hosts):
    row=create(signed_in,application='servicenow')
    resource=signed_in.get('/api/integrations/'+row['id']+'/resources').json()['items'][0]
    assert resource['path']=='/api/now/table/accounts'
    assert resource['record_path']=='result'
    assert resource['pagination']['offset_parameter']=='sysparm_offset'

def test_revoke_clears_credentials_and_blocks_access(signed_in,db,authorized_hosts):
    row=create(signed_in)
    assert signed_in.delete('/api/integrations/'+row['id']).status_code==200
    auth=db.scalar(select(IntegrationAuthConfiguration).where(IntegrationAuthConfiguration.connection_id==row['id']))
    assert auth.encrypted_secrets is None
    assert signed_in.post('/api/integrations/'+row['id']+'/test').status_code==422
    assert signed_in.get('/api/integrations').json()['total']==0

def test_signed_webhook_replay_and_rejection(signed_in,authorized_hosts):
    row=create(signed_in,webhook_secret='long-signing-secret')
    body=b'{"event":"changed"}';timestamp=str(int(datetime.now(timezone.utc).timestamp()))
    signature=hmac.new(b'long-signing-secret',timestamp.encode()+b'.'+body,hashlib.sha256).hexdigest()
    path='/api/integrations/'+row['id']+'/webhook'
    headers={'X-Reconcile-Timestamp':timestamp,'X-Reconcile-Signature':signature}
    assert signed_in.post(path,content=body,headers=headers).json()['status']=='accepted'
    assert signed_in.post(path,content=body,headers=headers).json()['status']=='duplicate'
    assert signed_in.post(path,content=body,headers={**headers,'X-Reconcile-Signature':'bad'}).status_code==403

def branch(**extra):
    import uuid
    return Branch(source_table_ids=[uuid.uuid4()],target_table_id=uuid.uuid4(),columns=[{'source':'source.id','target':'id'},{'source':'source.amount','target':'amount'}],business_keys=['id'],**extra)

def test_row_engine_tolerance_missing_extra_duplicate_and_nulls():
    source=[{'source.id':1,'source.amount':100,'__ids':{'s1'}},{'source.id':2,'source.amount':200,'__ids':{'s2'}},{'source.id':3,'source.amount':None,'__ids':{'s3'}}]
    target=[{'id':1,'amount':100.01},{'id':2,'amount':190},{'id':4,'amount':500}]
    stats,diffs,relevant,failed=compare(source,target,branch(numeric_tolerance=.02))
    assert (stats['matched'],stats['mismatched'],stats['missing'],stats['extra'])==(1,1,1,1)
    assert relevant=={'s1','s2','s3'} and failed=={'s2','s3'}
    stats,*_=compare(source,target+[{'id':1,'amount':100}],branch())
    assert stats['duplicates']==1

def test_aggregation_grain_and_distinct_coverage():
    import uuid
    rule=Branch(source_table_ids=[uuid.uuid4()],target_table_id=uuid.uuid4(),columns=[{'source':'s.date','target':'date'}],business_keys=['date'],aggregations=[{'source':'s.amount','target':'total','function':'SUM'}])
    source=[{'s.date':'2026-01-01','s.amount':10,'__ids':{'a'}},{'s.date':'2026-01-01','s.amount':20,'__ids':{'b'}}]
    stats,diffs,relevant,failed=compare(source,[{'date':'2026-01-01','total':30}],rule)
    assert stats['source_count']==stats['matched']==1 and relevant=={'a','b'} and not failed

def test_many_to_one_left_join_and_bounded_join():
    from app.schemas.reconciliation import Join
    data=[[{'customers.id':1,'__ids':{'a'}}],[{'accounts.customer_id':1,'accounts.amount':20,'__ids':{'b'}}]]
    rows=joined(data,[Join(left='customers.id',right='accounts.customer_id')],10)
    assert rows[0]['__ids']=={'a','b'} and rows[0]['accounts.amount']==20

@pytest.fixture
def data_engine(test_engine):
    from sqlalchemy import create_engine,text
    import uuid
    schema='data_'+uuid.uuid4().hex
    with test_engine.begin() as conn:conn.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine=create_engine(get_settings().database_url,connect_args={'options':f'-c search_path={schema}'})
    yield engine,schema
    engine.dispose()
    with test_engine.begin() as conn:conn.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))

def test_real_postgres_ingestion_mapping_and_reconciliation(signed_in,db,admin,authorized_hosts,monkeypatch,data_engine):
    engine,schema=data_engine
    @contextmanager
    def pg(*a,**k):yield engine
    monkeypatch.setattr(integration_data,'postgres_engine',pg)
    monkeypatch.setattr(adapters,'postgres_engine',pg)
    monkeypatch.setattr('app.api.data_workflows.postgres_engine',pg)
    source=create(signed_in,name='Live API')
    target=create(signed_in,name='Warehouse',application='postgresql',authentication_type='password',hostname='db.example.com',database_name='warehouse',schema_name=schema,username='writer',secret='db-secret',classification='target',resources=[])
    monkeypatch.setattr(adapters,'request_json',lambda *a,**k:{'data':[{'id':1,'amount':10},{'id':2,'amount':20}]})
    resource=signed_in.get('/api/integrations/'+source['id']+'/resources').json()['items'][0]
    assert signed_in.post(f"/api/integrations/{source['id']}/resources/{resource['id']}/approve-schema").status_code==200
    request={'resource_id':resource['id'],'destination_id':target['id'],'table_name':'api_accounts','schema_name':schema,'columns':['id','amount'],'layer':'bronze','mode':'full','confirmed':True}
    ingested=signed_in.post('/api/integrations/'+source['id']+'/ingest',json=request)
    assert ingested.status_code==200,ingested.text
    assert ingested.json()['record_count']==2
    datasets=signed_in.get('/api/workflows/tables').json()
    src=next(t for t in datasets if t['name']=='accounts');dst=next(t for t in datasets if t['name']=='api_accounts')
    mapping={'name':'API to warehouse','branches':[{'source_table_ids':[src['id']],'target_table_id':dst['id'],'columns':[{'source':'accounts.id','target':'id'},{'source':'accounts.amount','target':'amount'}],'business_keys':['id']}]}
    saved=signed_in.post('/api/mappings',json=mapping)
    assert saved.status_code==201,saved.text
    executed=signed_in.post('/api/reconciliation/run',json={'mapping_id':saved.json()['id']})
    assert executed.status_code==200,executed.text
    assert executed.json()['overall']['source_records_compared']==2
    results=signed_in.get('/api/reconciliation/runs/'+executed.json()['run_id']+'/results').json()
    assert results['items'][0]['matched']==2
    # Change a real destination amount and verify detailed differences.
    table=Table('api_accounts',MetaData(),schema=schema,autoload_with=engine)
    with engine.begin() as conn:conn.execute(table.update().where(table.c.id==2).values(amount=19))
    executed=signed_in.post('/api/reconciliation/run',json={'mapping_id':saved.json()['id']})
    diffs=signed_in.get('/api/reconciliation/runs/'+executed.json()['run_id']+'/differences').json()
    assert diffs['total']==1 and diffs['items'][0]['column_name']=='amount'

def test_incremental_ingestion_advances_watermark_once(signed_in,db,authorized_hosts,monkeypatch,data_engine):
    engine,schema=data_engine
    @contextmanager
    def pg(*a,**k):yield engine
    monkeypatch.setattr(integration_data,'postgres_engine',pg)
    source=create(signed_in);target=create(signed_in,name='Destination',application='postgresql',authentication_type='password',hostname='db.example.com',database_name='warehouse',schema_name=schema,username='writer',secret='db-secret',classification='target',resources=[])
    monkeypatch.setattr(adapters,'request_json',lambda *a,**k:{'data':[{'id':1},{'id':2}]})
    resource=signed_in.get('/api/integrations/'+source['id']+'/resources').json()['items'][0]
    request={'resource_id':resource['id'],'destination_id':target['id'],'table_name':'incremental_accounts','schema_name':schema,'columns':['id'],'mode':'incremental','watermark_column':'id','confirmed':True}
    result=signed_in.post('/api/integrations/'+source['id']+'/ingest',json=request)
    assert result.status_code==200,result.text
    assert result.json()['watermark']=='2'
    rerun=signed_in.post('/api/integrations/'+source['id']+'/ingest',json={**request,'configuration_id':result.json()['configuration_id']})
    assert rerun.status_code==200,rerun.text
    assert rerun.json()['record_count']==0
    # Recover a stale metadata watermark from the destination-side transaction ledger.
    from app.models import IntegrationSyncConfiguration
    config=db.get(IntegrationSyncConfiguration,result.json()['configuration_id'])
    config.watermark_value=None;db.commit()
    recovered=signed_in.post('/api/integrations/'+source['id']+'/ingest',json={**request,'configuration_id':result.json()['configuration_id']})
    assert recovered.status_code==200,recovered.text
    assert recovered.json()['record_count']==0 and recovered.json()['watermark']=='2'

def test_schema_failure_persists_sync_error(signed_in,db,authorized_hosts,monkeypatch):
    source=create(signed_in);target=create(signed_in,name='Warehouse',application='postgresql',authentication_type='password',hostname='db.example.com',database_name='warehouse',username='writer',secret='db-secret',resources=[])
    resource=signed_in.get('/api/integrations/'+source['id']+'/resources').json()['items'][0]
    monkeypatch.setattr(adapters,'request_json',lambda *a,**k:{'data':[{'id':1}]})
    request={'resource_id':resource['id'],'destination_id':target['id'],'table_name':'accounts','columns':['missing'],'confirmed':True}
    assert signed_in.post('/api/integrations/'+source['id']+'/ingest',json=request).status_code==422
    history=signed_in.get('/api/integrations/'+source['id']+'/sync-history').json()
    assert history['items'][0]['status']=='failed'
    assert 'missing' not in history['items'][0]['error_details'] or 'schema' in history['items'][0]['error_details']
