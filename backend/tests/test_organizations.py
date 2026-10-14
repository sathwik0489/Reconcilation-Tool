import uuid
from pathlib import Path
from alembic import command
from alembic.config import Config
from sqlalchemy import select,text
from app.models import (DatabaseConnection,RegisteredTable,TableMapping,MappingEdge,ReconciliationJob,ReconciliationRun,
    ReconciliationResult,OrganizationMembership,Organization,AuditLog)
from tests.conftest import make_user,sign_in
from tests.test_integrations import create,payload,authorized_hosts

def workspace(db,user,name='orders'):
    """Metadata an organization would own after connecting, mapping and running once."""
    org=user.organization_id
    connection=DatabaseConnection(organization_id=org,name='Warehouse '+name,connection_type='postgresql',classification='target',owner_id=user.id)
    db.add(connection);db.flush()
    table=RegisteredTable(organization_id=org,connection_id=connection.id,database_name='d',schema_name='public',table_name=name,classification='target')
    mapping=TableMapping(organization_id=org,name='Mapping '+name,mapping_type='one_to_one',owner_id=user.id)
    db.add_all([table,mapping]);db.flush()
    edge=MappingEdge(mapping_id=mapping.id,target_table_id=table.id)
    job=ReconciliationJob(organization_id=org,name='Job',mapping_id=mapping.id,owner_id=user.id)
    db.add_all([edge,job]);db.flush()
    run=ReconciliationRun(organization_id=org,job_id=job.id,executed_by=user.id,status='succeeded')
    db.add(run);db.flush()
    db.add(ReconciliationResult(run_id=run.id,edge_id=edge.id,source_count=5,target_count=5,matched=4,mismatched=1,missing=0,extra=0,duplicates=0))
    db.commit()
    return table,mapping,run

def test_session_reports_organization(signed_in,admin):
    me=signed_in.get('/api/auth/me').json()
    assert me['organization_id']==str(admin.organization_id) and me['organization_name']=='Test organization' and me['role']=='admin'

def test_metadata_is_invisible_to_other_organizations(signed_in,db,admin):
    table,mapping,run=workspace(db,admin)
    assert signed_in.get('/api/tables').json()['total']==1
    assert signed_in.get('/api/dashboard/summary').json()['matched']==4
    outsider=make_user(db,'outsider@example.com','admin','Outsider-password-1',organization_name='Rival')
    sign_in(signed_in,outsider,'Outsider-password-1')
    for path in ['/api/connections','/api/tables','/api/mappings','/api/reconciliation/runs','/api/ingestion/runs']:
        assert signed_in.get(path).json()['total']==0,path
    assert signed_in.get('/api/workflows/tables').json()==[]
    assert signed_in.get('/api/search?q=orders').json()==[]
    for path in [f'/api/tables/{table.id}',f'/api/tables/{table.id}/schema',f'/api/tables/{table.id}/preview',f'/api/mappings/{mapping.id}',
                 f'/api/reconciliation/runs/{run.id}',f'/api/reconciliation/runs/{run.id}/results',f'/api/reconciliation/runs/{run.id}/differences',f'/api/reconciliation/runs/{run.id}/export']:
        assert signed_in.get(path).status_code==404,path
    assert signed_in.post('/api/reconciliation/run',json={'mapping_id':str(mapping.id)}).status_code==404
    summary=signed_in.get('/api/dashboard/summary').json()
    assert summary['total_tables']==summary['connections']==summary['jobs']==summary['successful_runs']==summary['matched']==0
    assert summary['recent_tables']==[] and all(layer['tables']==0 for layer in summary['layers'])
    assert {a['action'] for a in summary['activity']}=={'auth.login'}
    assert all(m['count']==0 for m in signed_in.get('/api/system/status').json()['modules'])

def test_same_connection_name_allowed_in_two_organizations(signed_in,db,authorized_hosts):
    create(signed_in)
    outsider=make_user(db,'outsider@example.com','engineer','Outsider-password-1',organization_name='Rival')
    sign_in(signed_in,outsider,'Outsider-password-1')
    assert signed_in.post('/api/integrations',json=payload()).status_code==201
    assert signed_in.post('/api/integrations',json=payload()).status_code==409

def test_users_are_created_and_listed_per_organization(signed_in,db,admin):
    body={'email':'analyst@example.com','name':'Analyst','role':'analyst','password':'A-long-test-password'}
    created=signed_in.post('/api/settings/users',json=body)
    assert created.status_code==201,created.text
    assert created.json()['role']=='analyst' and created.json()['organization_id']==str(admin.organization_id)
    membership=db.scalar(select(OrganizationMembership).where(OrganizationMembership.user_id==uuid.UUID(created.json()['id'])))
    assert membership.organization_id==admin.organization_id and membership.role=='analyst'
    make_user(db,'outsider@example.com','viewer','Outsider-password-1',organization_name='Rival')
    listed=signed_in.get('/api/settings/users').json()
    assert listed['total']==2 and {u['email'] for u in listed['items']}=={'admin@example.com','analyst@example.com'}
    assert {u['role'] for u in listed['items']}=={'admin','analyst'}

def test_analyst_maps_and_runs_but_does_not_manage_connections(signed_in,db,admin,authorized_hosts):
    table,mapping,run=workspace(db,admin)
    analyst=make_user(db,'analyst@example.com','analyst','Analyst-password-1',organization=admin.organization_id)
    sign_in(signed_in,analyst,'Analyst-password-1')
    assert signed_in.post('/api/integrations',json=payload()).status_code==403
    assert signed_in.patch(f'/api/tables/{table.id}/layer?layer=gold').status_code==403
    # Permitted for analysts: the request passes the role check and fails only on its (empty) content.
    assert signed_in.post('/api/mappings',json={'name':'x','branches':[]}).status_code==422
    assert signed_in.get(f'/api/reconciliation/runs/{run.id}/results').status_code==200
    viewer=make_user(db,'viewer@example.com','viewer','Viewer-password-12',organization=admin.organization_id)
    sign_in(signed_in,viewer,'Viewer-password-12')
    assert signed_in.post('/api/mappings',json={'name':'x','branches':[]}).status_code==403
    assert signed_in.post('/api/reconciliation/run',json={'mapping_id':str(mapping.id)}).status_code==403
    assert signed_in.get(f'/api/reconciliation/runs/{run.id}/results').status_code==200

def test_only_administrators_rename_their_organization(signed_in,db,admin):
    assert signed_in.get('/api/organization').json()=={'id':str(admin.organization_id),'name':'Test organization','members':1}
    assert signed_in.patch('/api/organization',json={'name':'  Finance operations '}).json()['name']=='Finance operations'
    assert signed_in.get('/api/auth/me').json()['organization_name']=='Finance operations'
    assert signed_in.patch('/api/organization',json={'name':' '}).status_code==422
    engineer=make_user(db,'engineer@example.com','engineer','Engineer-password-1',organization=admin.organization_id)
    sign_in(signed_in,engineer,'Engineer-password-1')
    assert signed_in.patch('/api/organization',json={'name':'Mine'}).status_code==403

def test_removed_membership_ends_the_session(signed_in,db,admin):
    db.execute(OrganizationMembership.__table__.delete().where(OrganizationMembership.user_id==admin.id));db.commit()
    assert signed_in.get('/api/auth/me').status_code==401
    assert signed_in.post('/api/auth/login',json={'email':admin.email,'password':'Test-password-1234'}).status_code==401

def test_existing_installation_moves_into_default_organization(test_engine):
    config=Config(str(Path(__file__).resolve().parents[1]/'alembic.ini'))
    user_id,connection_id=uuid.uuid4(),uuid.uuid4()
    with test_engine.connect() as conn:
        config.attributes['connection']=conn
        command.downgrade(config,'0003_files');conn.commit()
        conn.execute(text("INSERT INTO users (id,email,name,password_hash,role,active,token_version,preferences) VALUES (:id,'legacy@example.com','Legacy','x','engineer',true,0,'{}')"),{'id':user_id})
        conn.execute(text("INSERT INTO database_connections (id,name,connection_type,classification,authentication_method,owner_id) VALUES (:id,'Legacy','csv','source','file',:owner)"),{'id':connection_id,'owner':user_id})
        conn.execute(text("INSERT INTO audit_logs (id,actor_id,action,resource_type,details) VALUES (:a,:actor,'auth.login','user','{}'),(:b,NULL,'auth.login_failed','user','{}')"),{'a':uuid.uuid4(),'b':uuid.uuid4(),'actor':user_id})
        conn.commit()
        try:
            command.upgrade(config,'head');conn.commit()
            organization,role=conn.execute(text('SELECT organization_id,role FROM organization_memberships WHERE user_id=:id'),{'id':user_id}).one()
            assert role=='engineer'
            assert conn.execute(text('SELECT name FROM organizations WHERE id=:id'),{'id':organization}).scalar()=='Default organization'
            assert conn.execute(text('SELECT organization_id FROM database_connections WHERE id=:id'),{'id':connection_id}).scalar()==organization
            assert conn.execute(text('SELECT action,organization_id FROM audit_logs ORDER BY action')).all()==[('auth.login',organization),('auth.login_failed',None)]
            # The previous three-role layout is restored on rollback.
            command.downgrade(config,'0003_files');conn.commit()
            assert conn.execute(text('SELECT role FROM users WHERE id=:id'),{'id':user_id}).scalar()=='engineer'
        finally:
            conn.rollback()
            for table in ['audit_logs','database_connections','users']:conn.execute(text(f'DELETE FROM {table}'))
            conn.commit()
            command.upgrade(config,'head');conn.commit()
