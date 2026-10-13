import json
import uuid
from contextlib import contextmanager
from pathlib import Path
import pytest
from sqlalchemy import Table,MetaData,select
from app.core.config import get_settings
from app.core.security import decrypt_credential
from app.models import IntegrationConnection
from app.connectors import integrations as adapters
from app.services import integration_data
from tests.test_integrations import create,authorized_hosts,data_engine

def test_fan_out_unique_coverage_and_csv_export(signed_in,db,authorized_hosts,monkeypatch,data_engine):
    engine,schema=data_engine
    @contextmanager
    def pg(*args,**kwargs):yield engine
    monkeypatch.setattr(integration_data,'postgres_engine',pg);monkeypatch.setattr(adapters,'postgres_engine',pg)
    source=create(signed_in);destination=create(signed_in,name='Warehouse',application='postgresql',authentication_type='password',hostname='db.example.com',database_name='warehouse',schema_name=schema,username='writer',secret='secret',resources=[],classification='target')
    monkeypatch.setattr(adapters,'request_json',lambda *a,**k:{'data':[{'id':1,'amount':10},{'id':2,'amount':20}]})
    resource=signed_in.get('/api/integrations/'+source['id']+'/resources').json()['items'][0]
    for name in ['payments','settlements']:
        result=signed_in.post('/api/integrations/'+source['id']+'/ingest',json={'resource_id':resource['id'],'destination_id':destination['id'],'table_name':name,'schema_name':schema,'columns':['id','amount'],'confirmed':True})
        assert result.status_code==200,result.text
    tables=signed_in.get('/api/workflows/tables').json();src=next(t for t in tables if t['name']=='accounts')
    targets=[t for t in tables if t['name'] in ['payments','settlements']]
    assert {t['layer'] for t in targets}=={'bronze'}
    saved=signed_in.post('/api/mappings',json={'name':'Fan out','branches':[{'source_table_ids':[src['id']],'target_table_id':t['id'],'columns':[{'source':'accounts.id','target':'id'},{'source':'accounts.amount','target':'amount'}],'business_keys':['id']} for t in targets]})
    assert saved.status_code==201,saved.text
    assert saved.json()['mapping_type']=='one_to_many'
    result=signed_in.post('/api/reconciliation/run',json={'mapping_id':saved.json()['id']})
    assert result.json()['overall']['source_records_compared']==2
    assert result.json()['overall']['source_records_matched_all_relevant_targets']==2
    table=Table('settlements',MetaData(),schema=schema,autoload_with=engine)
    with engine.begin() as conn:conn.execute(table.update().where(table.c.id==2).values(amount=19))
    result=signed_in.post('/api/reconciliation/run',json={'mapping_id':saved.json()['id']})
    assert result.json()['overall']['source_records_compared']==2
    assert result.json()['overall']['source_records_with_differences']==1
    exported=signed_in.get('/api/reconciliation/runs/'+result.json()['run_id']+'/export')
    assert exported.status_code==200 and 'attachment' in exported.headers['content-disposition']
    assert 'mismatched' in exported.text and 'amount' in exported.text

def test_csv_ingestion_uses_encrypted_storage_and_real_postgres(signed_in,db,authorized_hosts,monkeypatch,data_engine):
    engine,schema=data_engine
    @contextmanager
    def pg(*args,**kwargs):yield engine
    monkeypatch.setattr(integration_data,'postgres_engine',pg)
    folder=Path(__file__).resolve().parents[2]/'.runtime'/('test-files-'+uuid.uuid4().hex)
    monkeypatch.setattr(get_settings(),'dataset_storage',str(folder))
    destination=create(signed_in,name='Warehouse',application='postgresql',authentication_type='password',hostname='db.example.com',database_name='warehouse',schema_name=schema,username='writer',secret='secret',resources=[],classification='target')
    config={'destination_id':destination['id'],'table_name':'csv_accounts','schema_name':schema,'columns':['id','name'],'confirmed':True}
    try:
        response=signed_in.post('/api/integrations/files/ingest',data={'configuration':json.dumps(config)},files={'file':('accounts.csv',b'id,name\n1,Alice\n2,Bob\n','text/csv')})
        assert response.status_code==200,response.text
        assert response.json()['record_count']==2
        files=list(folder.glob('*.enc'));assert len(files)==1
        assert 'Alice' not in files[0].read_text()
        assert 'Alice' in decrypt_credential(files[0].read_text())
        table=Table('csv_accounts',MetaData(),schema=schema,autoload_with=engine)
        with engine.connect() as conn:assert len(conn.execute(select(table)).all())==2
    finally:
        if folder.exists():
            for file in folder.glob('*.enc'):file.unlink()
            folder.rmdir()

def test_failed_csv_import_leaves_no_file_or_usable_source(signed_in,db,authorized_hosts,monkeypatch):
    folder=Path(__file__).resolve().parents[2]/'.runtime'/('test-files-'+uuid.uuid4().hex)
    monkeypatch.setattr(get_settings(),'dataset_storage',str(folder))
    destination=create(signed_in,name='Warehouse',application='postgresql',authentication_type='password',hostname='db.example.com',database_name='warehouse',username='writer',secret='secret',resources=[],classification='target')
    config={'destination_id':destination['id'],'table_name':'csv_accounts','columns':['id','name'],'confirmed':True}
    try:
        # The destination host is not authorized for execution, so ingestion fails after the upload is stored.
        response=signed_in.post('/api/integrations/files/ingest',data={'configuration':json.dumps(config)},files={'file':('accounts.csv',b'id,name\n1,Alice\n','text/csv')})
        assert response.status_code==422,response.text
        assert not list(folder.glob('*.enc'))
        assert [r['id'] for r in signed_in.get('/api/integrations').json()['items']]==[destination['id']]
        unknown={**config,'columns':['id','absent']}
        assert signed_in.post('/api/integrations/files/ingest',data={'configuration':json.dumps(unknown)},files={'file':('accounts.csv',b'id,name\n1,Alice\n','text/csv')}).status_code==422
        assert not list(folder.glob('*.enc'))
    finally:
        if folder.exists():
            for file in folder.glob('*.enc'):file.unlink()
            folder.rmdir()

def test_invalid_preview_input_is_not_a_connection_error(signed_in,authorized_hosts,monkeypatch):
    row=create(signed_in)
    monkeypatch.setattr(adapters,'request_json',lambda *a,**k:{'data':[{'id':1}]})
    resource=signed_in.get('/api/integrations/'+row['id']+'/resources').json()['items'][0]
    assert signed_in.get(f"/api/integrations/{row['id']}/preview?resource_id={resource['id']}&filters=%7B%22unauthorized%22%3A1%7D").status_code==422
    detail=signed_in.get('/api/integrations/'+row['id']).json()
    assert detail['status']=='configured' and detail['errors']==[]
