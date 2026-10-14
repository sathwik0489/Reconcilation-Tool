from collections import defaultdict, deque
from functools import lru_cache
from pathlib import Path
from threading import Lock
from time import monotonic
from uuid import UUID
from secrets import token_urlsafe
from urllib.parse import urlsplit
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from sqlalchemy import select, func, text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.core.config import get_settings
from app.core.security import (current_user, admin_user, password_hasher, dummy_hash, make_token,
                               membership_for, enter_organization)
from app.models import (User, DatabaseConnection, RegisteredTable, TableColumn, IngestionRun,
                        TableMapping, ReconciliationRun, ReconciliationJob, MedallionLayer, AuditLog,
                        Organization, OrganizationMembership)
from app.schemas.foundation import Login, UserView, Preferences, CreateUser, OrganizationUpdate
from app.services.audit import audit
from app.repositories.dashboard import summary

router = APIRouter(prefix='/api')
attempts = defaultdict(deque)
attempt_lock = Lock()

@router.get('/auth/options', tags=['Authentication'])
def auth_options():
    settings = get_settings()
    return {'development_login': settings.app_env == 'development' and settings.allow_development_login}

@router.post('/auth/development-login', response_model=UserView, tags=['Authentication'])
def development_login(request: Request, response: Response, db: Session = Depends(get_db)):
    settings = get_settings()
    if not settings.allow_development_login or settings.app_env != 'development':
        raise HTTPException(404, 'Development login is disabled')
    if urlsplit(request.headers.get('origin', '')).hostname not in {'localhost', '127.0.0.1', '::1'}:
        raise HTTPException(403, 'Development login requires a local browser')
    # Serialize first-time provisioning, without reusing an administrator account.
    db.execute(select(func.pg_advisory_xact_lock(func.hashtext('reconviora:local-preview'))))
    preview_id = UUID('662649ad-b475-45a6-a1c9-6116ef084ce3')
    user = db.get(User, preview_id)
    if user is None:
        if db.scalar(select(User).where(User.email == 'local-preview@example.com')):
            raise HTTPException(409, 'Local preview account is unavailable')
        user = User(id=preview_id, email='local-preview@example.com', name='Local Preview',
                    password_hash=password_hasher.hash(token_urlsafe(48)), preferences={})
        db.add(user)
        db.flush()
    membership = membership_for(db, user)
    if membership is None:
        # A separate organization: skipping sign-in never opens another organization's workspace.
        organization = Organization(name='Local Preview')
        db.add(organization)
        db.flush()
        membership = OrganizationMembership(organization_id=organization.id, user_id=user.id, role='engineer')
        db.add(membership)
        db.flush()
    if not user.active or membership.role != 'engineer' or user.email != 'local-preview@example.com':
        raise HTTPException(403, 'Local preview account is unavailable')
    enter_organization(db, user, membership)
    audit(db, 'auth.development_login', user.id, resource_id=user.id)
    db.commit()
    response.set_cookie('recon_session', make_token(user), httponly=True, secure=settings.cookie_secure,
                        samesite='lax', max_age=settings.session_minutes*60, path='/')
    response.headers['Cache-Control'] = 'no-store'
    return user

@router.get('/health/live', tags=['Health'])
def live():
    return {'status': 'ok', 'service': 'reconcile-api'}

@lru_cache
def migration_head():
    # Read the expected revision from the migration scripts so new migrations need no code change here.
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    return ScriptDirectory.from_config(Config(str(Path(__file__).resolve().parents[2] / 'alembic.ini'))).get_current_head()

@router.get('/health/ready', tags=['Health'])
def ready(db: Session = Depends(get_db)):
    try:
        db.execute(text('SELECT 1'))
        version = db.scalar(text('SELECT version_num FROM alembic_version'))
        layers = db.scalar(select(func.count()).select_from(MedallionLayer))
        if version != migration_head() or layers != 3:
            raise HTTPException(503, 'Metadata migrations are not ready')
        return {'status':'ok','database':'postgresql','migration':version,'phase':1}
    except SQLAlchemyError:
        raise HTTPException(503, 'Metadata database unavailable or migrations not applied') from None

@router.post('/auth/login', response_model=UserView, tags=['Authentication'])
def login(payload: Login, request: Request, response: Response, db: Session = Depends(get_db)):
    key = request.client.host if request.client else 'unknown'
    now = monotonic()
    with attempt_lock:
        # Bounded in-process protection; production gateway must also rate-limit.
        if len(attempts) > 10000:
            attempts.clear()
        recent = attempts[key]
        while recent and now-recent[0] > 60:
            recent.popleft()
        if len(recent) >= 10:
            raise HTTPException(429, 'Too many attempts. Try again in a minute.', headers={'Retry-After':'60'})
        recent.append(now)
    user = db.scalar(select(User).where(User.email == str(payload.email).lower()))
    valid = password_hasher.verify(payload.password, user.password_hash if user else dummy_hash)
    membership = membership_for(db, user) if user else None
    if not user or not valid or not user.active or not membership:
        audit(db, 'auth.login_failed')
        db.commit()
        raise HTTPException(401, 'Email or password is incorrect')
    enter_organization(db, user, membership)
    audit(db, 'auth.login', user.id, resource_id=user.id)
    db.commit()
    settings = get_settings()
    response.set_cookie('recon_session', make_token(user), httponly=True, secure=settings.cookie_secure,
                        samesite='lax', max_age=settings.session_minutes*60, path='/')
    response.headers['Cache-Control'] = 'no-store'
    return user

@router.get('/auth/me', response_model=UserView, tags=['Authentication'])
def me(user: User = Depends(current_user)):
    return user

@router.post('/auth/logout', status_code=204, tags=['Authentication'])
def logout(response: Response, user: User = Depends(current_user), db: Session = Depends(get_db)):
    user.token_version += 1
    audit(db, 'auth.logout', user.id, resource_id=user.id)
    db.commit()
    response.delete_cookie('recon_session', path='/', secure=get_settings().cookie_secure, httponly=True, samesite='lax')

@router.patch('/settings/preferences', response_model=UserView, tags=['Settings'])
def preferences(payload: Preferences, user: User = Depends(current_user), db: Session = Depends(get_db)):
    user.name = payload.display_name.strip()
    if not user.name:
        raise HTTPException(422, 'Display name cannot be blank')
    user.preferences = payload.model_dump(exclude={'display_name'})
    audit(db, 'settings.updated', user.id, resource_id=user.id)
    db.commit()
    return user

@router.get('/dashboard/summary', tags=['Dashboard'])
def dashboard(db: Session = Depends(get_db), user: User = Depends(current_user)):
    return summary(db,user)

@router.get('/system/status', tags=['System'])
def status(db: Session = Depends(get_db), user: User = Depends(current_user)):
    health = ready(db)
    def total(model):
        return db.scalar(select(func.count()).select_from(model).where(model.organization_id == user.organization_id))
    return {**health, 'modules': [
        {'id': 'connections','name': 'Data Connections','phase':2,'description':'Configure and test database connectors.', 'count':total(DatabaseConnection)},
        {'id': 'ingestion','name': 'Data Ingestion','phase':2,'description':'Upload files and move authorized datasets.', 'count':total(IngestionRun)},
        {'id': 'tables','name': 'Table Management','phase':2,'description':'Create and register source and target tables.', 'count':total(RegisteredTable)},
        {'id': 'mapping','name': 'Visual Table Mapping','phase':3,'description':'Connect tables, columns, and Medallion layers.', 'count':total(TableMapping)},
        {'id': 'reconciliation','name': 'Reconciliation','phase':4,'description':'Execute rules and investigate data differences.', 'count':total(ReconciliationRun)},
        {'id': 'explorer','name': 'Data Explorer','phase':5,'description':'Search, filter, and compare authorized datasets.', 'count':total(RegisteredTable)},
        {'id': 'reports','name': 'Reports & Visualization','phase':5,'description':'Analyze run history and export detailed reports.', 'count':total(ReconciliationRun)}]}

def inventory(model, fields, db, page, page_size, search, search_field, organization_id):
    query = select(model).where(model.organization_id == organization_id)
    if search:
        query = query.where(search_field.ilike('%' + search.replace('\\','\\\\').replace('%','\\%').replace('_','\\_') + '%', escape='\\'))
    total = db.scalar(select(func.count()).select_from(query.subquery()))
    rows = db.scalars(query.order_by(model.created_at.desc(), model.id).offset((page-1)*page_size).limit(page_size)).all()
    return {'items': [{field: getattr(row, field) for field in fields} for row in rows],
            'total': total, 'page':page,'page_size':page_size}

INVENTORIES = {
    'connections': (DatabaseConnection, ['id','name','connection_type','classification','description','created_at'], DatabaseConnection.name),
    'tables': (RegisteredTable, ['id','table_name','schema_name','classification','layer_id','created_at'], RegisteredTable.table_name),
    'ingestion/runs': (IngestionRun, ['id','status','record_count','created_at'], IngestionRun.status),
    'mappings': (TableMapping, ['id','name','mapping_type','version','created_at'], TableMapping.name),
    'reconciliation/runs': (ReconciliationRun, ['id','job_id','status','duration_ms','created_at'], ReconciliationRun.status),
}

def make_list(model, fields, search_field):
    def list_records(page: int = Query(1, ge=1), page_size: int = Query(25, ge=1, le=100),
                     search: str = Query('', max_length=100), db: Session = Depends(get_db), user: User = Depends(current_user)):
        return inventory(model, fields, db, page, page_size, search, search_field, user.organization_id)
    return list_records

for path, (model, fields, field) in INVENTORIES.items():
    router.add_api_route('/'+path, make_list(model, fields, field), methods=['GET'], tags=['Metadata inventories'], name=path.replace('/','_'))

@router.get('/tables/{table_id}', tags=['Metadata inventories'])
def table_detail(table_id: UUID, db: Session = Depends(get_db), user: User = Depends(current_user)):
    row = db.get(RegisteredTable, table_id)
    if not row or row.organization_id != user.organization_id:
        raise HTTPException(404, 'Table not found')
    return {field: getattr(row, field) for field in ['id','connection_id','database_name','schema_name','table_name','description','classification','layer_id','created_at','updated_at']}

@router.get('/tables/{table_id}/schema', tags=['Metadata inventories'])
def table_schema(table_id: UUID, db: Session = Depends(get_db), user: User = Depends(current_user)):
    table_detail(table_id, db, user)
    rows = db.scalars(select(TableColumn).where(TableColumn.table_id == table_id).order_by(TableColumn.ordinal)).all()
    return {'items':[{f:getattr(row,f) for f in ['id','name','data_type','ordinal','primary_key','nullable','default_value']} for row in rows]}

@router.get('/settings/users', tags=['Administration'])
def users(page: int = Query(1, ge=1), page_size: int = Query(25, ge=1, le=100), db: Session = Depends(get_db), user: User = Depends(admin_user)):
    members = select(User, OrganizationMembership.role).join(OrganizationMembership, OrganizationMembership.user_id == User.id).where(
        OrganizationMembership.organization_id == user.organization_id)
    total = db.scalar(select(func.count()).select_from(members.subquery()))
    rows = db.execute(members.order_by(User.created_at.desc(), User.id).offset((page-1)*page_size).limit(page_size)).all()
    return {'items': [{'id': row.id, 'email': row.email, 'name': row.name, 'role': role, 'active': row.active, 'created_at': row.created_at} for row, role in rows],
            'total': total, 'page': page, 'page_size': page_size}

@router.get('/organization', tags=['Administration'])
def organization_detail(db: Session = Depends(get_db), user: User = Depends(current_user)):
    members = db.scalar(select(func.count()).select_from(OrganizationMembership).where(OrganizationMembership.organization_id == user.organization_id))
    return {'id': user.organization_id, 'name': user.organization_name, 'members': members}

@router.patch('/organization', tags=['Administration'])
def rename_organization(payload: OrganizationUpdate, db: Session = Depends(get_db), user: User = Depends(admin_user)):
    organization = db.get(Organization, user.organization_id)
    organization.name = payload.name
    audit(db, 'organization.renamed', user.id, 'organization', organization.id)
    db.commit()
    return {'id': organization.id, 'name': organization.name}

@router.post('/settings/users', response_model=UserView, status_code=201, tags=['Administration'])
def create_user(payload: CreateUser, db: Session = Depends(get_db), user: User = Depends(admin_user)):
    row = User(email=str(payload.email).lower(), name=payload.name.strip(),
               password_hash=password_hasher.hash(payload.password), preferences={})
    if not row.name:
        raise HTTPException(422, 'Name cannot be blank')
    db.add(row)
    try:
        db.flush()
        # New accounts join the administrator's organization with the chosen role.
        membership = OrganizationMembership(organization_id=user.organization_id, user_id=row.id, role=payload.role)
        db.add(membership)
        db.flush()
        audit(db, 'user.created', user.id, resource_id=row.id, details={'role': payload.role})
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, 'Email is already registered') from None
    return enter_organization(db, row, membership)
