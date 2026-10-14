"""Integration tests use a disposable schema on real PostgreSQL, never SQLite."""
import uuid
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from alembic import command
from alembic.config import Config
from app.core.config import get_settings
from app.core.database import get_db
from app.core.security import password_hasher
from app.main import app
from app.models import User,Organization,OrganizationMembership
from pathlib import Path

@pytest.fixture(scope='session')
def test_engine():
    schema='test_'+uuid.uuid4().hex
    admin=create_engine(get_settings().database_url)
    with admin.begin() as conn:
        conn.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine=create_engine(get_settings().database_url,connect_args={'options':f'-c search_path={schema} -c statement_timeout=10000'})
    config=Config(str(Path(__file__).resolve().parents[1]/'alembic.ini'))
    # Reuse the normal migration environment with an isolated connection.
    with engine.connect() as conn:
        config.attributes['connection']=conn
        command.upgrade(config,'head')
    yield engine
    engine.dispose()
    with admin.begin() as conn:
        conn.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
    admin.dispose()

@pytest.fixture
def db(test_engine):
    # Outer transaction rolls back account/data mutations between tests.
    connection=test_engine.connect()
    transaction=connection.begin()
    factory=sessionmaker(bind=connection,expire_on_commit=False,join_transaction_mode='create_savepoint')
    with factory() as session:
        yield session
    transaction.rollback()
    connection.close()

@pytest.fixture
def client(db):
    from app.api.foundation import attempts
    attempts.clear()
    def override():yield db
    app.dependency_overrides[get_db]=override
    with TestClient(app,headers={'Origin':'http://localhost:3000'}) as c:
        yield c
    app.dependency_overrides.clear()

def make_user(db,email,role,password,organization=None,organization_name='Test organization'):
    """Create an account with one membership; a new organization is created unless one is given."""
    if organization is None:
        organization=Organization(name=organization_name);db.add(organization);db.flush()
    user=User(email=email,name=email.split('@')[0].title(),password_hash=password_hasher.hash(password),preferences={})
    db.add(user);db.flush()
    db.add(OrganizationMembership(organization_id=getattr(organization,'id',organization),user_id=user.id,role=role));db.commit()
    user.organization_id=getattr(organization,'id',organization);user.role=role
    return user

def sign_in(client,user,password):
    client.post('/api/auth/logout')
    assert client.post('/api/auth/login',json={'email':user.email,'password':password}).status_code==200

@pytest.fixture
def admin(db):
    return make_user(db,'admin@example.com','admin','Test-password-1234')

@pytest.fixture
def signed_in(client,admin):
    assert client.post('/api/auth/login',json={'email':admin.email,'password':'Test-password-1234'}).status_code==200
    return client
