import uuid
from datetime import datetime,timezone
from sqlalchemy import select,inspect
from app.models import (Base,User,DatabaseConnection,MedallionLayer,RegisteredTable,
                        TableMapping,MappingEdge,ReconciliationJob,ReconciliationRun,ReconciliationResult,AuditLog)
from app.core.security import encrypt_credential,decrypt_credential,password_hasher
from app.core.config import Settings,get_settings
from alembic import command
from alembic.config import Config
from pathlib import Path
import pytest

def test_all_metadata_tables_migrated(test_engine):
    assert set(Base.metadata.tables).issubset(set(inspect(test_engine).get_table_names()))

def test_health(client):
    assert client.get('/api/health/live').json()['status']=='ok'
    assert client.get('/api/health/ready').json()['database']=='postgresql'

def test_protected_routes(client):
    for path in ['/auth/me','/dashboard/summary','/system/status','/connections','/tables','/mappings','/reconciliation/runs','/settings/users']:
        assert client.get('/api'+path).status_code==401

def test_auth_cookie_and_no_password(signed_in):
    response=signed_in.get('/api/auth/me')
    assert response.status_code==200
    assert set(response.json())=={'id','email','name','role','preferences'}
    assert signed_in.cookies.get('recon_session')

def test_cookie_flags(client,admin):
    result=client.post('/api/auth/login',json={'email':admin.email,'password':'Test-password-1234'})
    header=result.headers['set-cookie'].lower()
    assert 'httponly' in header and 'samesite=lax' in header

def test_wrong_login_is_generic(client,admin):
    a=client.post('/api/auth/login',json={'email':admin.email,'password':'bad'})
    b=client.post('/api/auth/login',json={'email':'unknown@example.com','password':'bad'})
    assert a.status_code==b.status_code==401
    assert a.json()==b.json()

def test_csrf_origin_required(client,admin):
    assert client.post('/api/auth/login',headers={'Origin':'https://evil.example'},json={'email':admin.email,'password':'Test-password-1234'}).status_code==403

def test_logout_revokes_replayed_cookie(signed_in):
    token=signed_in.cookies.get('recon_session')
    assert signed_in.post('/api/auth/logout').status_code==204
    signed_in.cookies.set('recon_session',token)
    assert signed_in.get('/api/auth/me').status_code==401

def test_preferences_persist_and_validate(signed_in,db,admin):
    body={'display_name':'Alex','page_size':50,'compact_tables':True}
    assert signed_in.patch('/api/settings/preferences',json=body).status_code==200
    assert signed_in.get('/api/auth/me').json()['preferences']['page_size']==50
    assert db.get(User,admin.id).name=='Alex'
    assert signed_in.patch('/api/settings/preferences',json={**body,'page_size':999}).status_code==422
    assert db.scalar(select(AuditLog.id).where(AuditLog.action=='settings.updated'))

def test_admin_user_creation_and_duplicate(signed_in):
    body={'email':'viewer@example.com','name':'Viewer','role':'viewer','password':'A-long-test-password'}
    result=signed_in.post('/api/settings/users',json=body)
    assert result.status_code==201
    assert 'password_hash' not in result.json()
    assert signed_in.post('/api/settings/users',json=body).status_code==409

def test_viewer_cannot_manage_users(client,db):
    user=User(email='viewer@example.com',name='Viewer',role='viewer',password_hash=password_hasher.hash('Viewer-password-1234'),preferences={})
    db.add(user);db.commit()
    assert client.post('/api/auth/login',json={'email':user.email,'password':'Viewer-password-1234'}).status_code==200
    assert client.get('/api/settings/users').status_code==403
    assert client.post('/api/settings/users',json={'email':'x@example.com','name':'X','password':'long-password-123','role':'admin'}).status_code==403

def test_live_dashboard_and_safe_connection_inventory(signed_in,db,admin):
    credential=encrypt_credential('secret-password')
    connection=DatabaseConnection(name='Test source',connection_type='postgresql',classification='source',owner_id=admin.id,encrypted_credential=credential)
    db.add(connection);db.flush()
    layer=db.scalar(select(MedallionLayer).where(MedallionLayer.name=='bronze'))
    table=RegisteredTable(connection_id=connection.id,database_name='sample',schema_name='bronze',table_name='source_transactions',classification='source',layer_id=layer.id)
    db.add(table);db.commit()
    summary=signed_in.get('/api/dashboard/summary').json()
    assert summary['total_tables']==summary['source_tables']==1
    assert summary['target_tables']==0 and summary['connections']==1
    assert summary['match_percentage'] is None
    assert summary['layers'][0]['tables']==1
    inventory=signed_in.get('/api/connections').json()
    assert inventory['total']==1
    assert 'encrypted_credential' not in inventory['items'][0]
    assert 'secret-password' not in str(inventory)

def test_pagination_and_search(signed_in,db,admin):
    for name in ['Alpha','Beta','Gamma']:
        db.add(DatabaseConnection(name=name,connection_type='csv',classification='source',owner_id=admin.id))
    db.commit()
    result=signed_in.get('/api/connections?page=2&page_size=2').json()
    assert result['total']==3 and len(result['items'])==1
    assert signed_in.get('/api/connections?search=alpha').json()['total']==1
    assert signed_in.get('/api/connections?search=%25').json()['total']==0
    assert signed_in.get('/api/connections?page_size=1000').status_code==422

def test_invalid_table_identifiers(signed_in):
    assert signed_in.get('/api/tables/not-a-uuid').status_code==422
    assert signed_in.get('/api/tables/'+str(uuid.uuid4())).status_code==404

def test_encryption_is_authenticated():
    encrypted=encrypt_credential('example-secret')
    assert encrypted!='example-secret'
    assert decrypt_credential(encrypted)=='example-secret'

def test_production_requires_https_and_secure_cookies():
    settings=get_settings()
    with pytest.raises(ValueError):
        Settings(_env_file=None,database_url=settings.database_url,jwt_secret=settings.jwt_secret,credential_key=settings.credential_key,app_env='production',cookie_secure=False)

def test_latest_run_metrics_do_not_repeat_history(signed_in,db,admin):
    conn=DatabaseConnection(name='Conn',connection_type='csv',classification='source',owner_id=admin.id)
    db.add(conn);db.flush()
    table=RegisteredTable(connection_id=conn.id,database_name='d',schema_name='s',table_name='t',classification='target')
    mapping=TableMapping(name='Mapping',mapping_type='one_to_one',owner_id=admin.id)
    db.add_all([table,mapping]);db.flush()
    edge=MappingEdge(mapping_id=mapping.id,target_table_id=table.id)
    job=ReconciliationJob(name='Job',mapping_id=mapping.id,owner_id=admin.id)
    db.add_all([edge,job]);db.flush()
    for year,matched in [(2025,50),(2026,8)]:
        run=ReconciliationRun(job_id=job.id,executed_by=admin.id,status='succeeded',created_at=datetime(year,1,1,tzinfo=timezone.utc))
        db.add(run);db.flush()
        db.add(ReconciliationResult(run_id=run.id,edge_id=edge.id,source_count=10,target_count=10,matched=matched,mismatched=2,missing=0,extra=0,duplicates=0))
    db.commit()
    result=signed_in.get('/api/dashboard/summary').json()
    assert result['matched']==8 and result['successful_runs']==2 and result['match_percentage']==80

def test_migration_downgrade_upgrade_round_trip(test_engine):
    config=Config(str(Path(__file__).resolve().parents[1]/'alembic.ini'))
    with test_engine.connect() as conn:
        config.attributes['connection']=conn
        command.downgrade(config,'base')
        assert not set(Base.metadata.tables).intersection(inspect(conn).get_table_names())
        conn.commit()
        command.upgrade(config,'head')
        assert set(Base.metadata.tables).issubset(inspect(conn).get_table_names())
        conn.commit()
