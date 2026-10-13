import uuid
from datetime import datetime
from sqlalchemy import String, Text, ForeignKey, DateTime, Integer, UniqueConstraint, CheckConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped,mapped_column
from app.models.metadata import Base,Identity

class ExternalApplication(Identity,Base):
    __tablename__='external_applications'
    slug: Mapped[str]=mapped_column(String(40),unique=True)
    name: Mapped[str]=mapped_column(String(100))
    description: Mapped[str]=mapped_column(Text)
    authentication_methods: Mapped[list]=mapped_column(JSONB)

class IntegrationConnection(Identity,Base):
    __tablename__='integration_connections'
    application_id: Mapped[uuid.UUID]=mapped_column(ForeignKey('external_applications.id'))
    owner_id: Mapped[uuid.UUID]=mapped_column(ForeignKey('users.id'),index=True)
    database_connection_id: Mapped[uuid.UUID]=mapped_column(ForeignKey('database_connections.id'))
    name: Mapped[str]=mapped_column(String(100))
    authentication_type: Mapped[str]=mapped_column(String(30))
    status: Mapped[str]=mapped_column(String(20),default='configured')
    configuration: Mapped[dict]=mapped_column(JSONB)
    authorized_scopes: Mapped[list]=mapped_column(JSONB,default=list)
    last_successful_sync: Mapped[datetime|None]=mapped_column(DateTime(timezone=True))
    last_failed_sync: Mapped[datetime|None]=mapped_column(DateTime(timezone=True))
    __table_args__=(UniqueConstraint('owner_id','name'),CheckConstraint("status IN ('configured','connected','error','revoked')",name='integration_status'))

class IntegrationAuthConfiguration(Identity,Base):
    __tablename__='integration_auth_configurations'
    connection_id: Mapped[uuid.UUID]=mapped_column(ForeignKey('integration_connections.id'),unique=True)
    encrypted_secrets: Mapped[str|None]=mapped_column(Text)
    state_hash: Mapped[str|None]=mapped_column(String(64),unique=True)
    state_expires_at: Mapped[datetime|None]=mapped_column(DateTime(timezone=True))
    token_expires_at: Mapped[datetime|None]=mapped_column(DateTime(timezone=True))

class IntegrationResource(Identity,Base):
    __tablename__='integration_resources'
    connection_id: Mapped[uuid.UUID]=mapped_column(ForeignKey('integration_connections.id'),index=True)
    registered_table_id: Mapped[uuid.UUID|None]=mapped_column(ForeignKey('registered_tables.id'))
    name: Mapped[str]=mapped_column(String(128))
    path: Mapped[str]=mapped_column(String(512))
    record_path: Mapped[str]=mapped_column(String(128),default='')
    pagination: Mapped[dict]=mapped_column(JSONB,default=dict)
    schema_snapshot: Mapped[list]=mapped_column(JSONB,default=list)
    schema_version: Mapped[int]=mapped_column(Integer,default=0)
    available_filters: Mapped[list]=mapped_column(JSONB,default=list)
    __table_args__=(UniqueConstraint('connection_id','name'),)

class IntegrationSyncConfiguration(Identity,Base):
    __tablename__='integration_sync_configurations'
    connection_id: Mapped[uuid.UUID]=mapped_column(ForeignKey('integration_connections.id'))
    resource_id: Mapped[uuid.UUID]=mapped_column(ForeignKey('integration_resources.id'))
    destination_id: Mapped[uuid.UUID]=mapped_column(ForeignKey('integration_connections.id'))
    registered_table_id: Mapped[uuid.UUID|None]=mapped_column(ForeignKey('registered_tables.id'))
    configuration: Mapped[dict]=mapped_column(JSONB)
    watermark_value: Mapped[str|None]=mapped_column(Text)

class IntegrationSyncRun(Identity,Base):
    __tablename__='integration_sync_runs'
    connection_id: Mapped[uuid.UUID]=mapped_column(ForeignKey('integration_connections.id'),index=True)
    configuration_id: Mapped[uuid.UUID]=mapped_column(ForeignKey('integration_sync_configurations.id'))
    executed_by: Mapped[uuid.UUID]=mapped_column(ForeignKey('users.id'))
    status: Mapped[str]=mapped_column(String(20))
    record_count: Mapped[int]=mapped_column(Integer,default=0)
    started_at: Mapped[datetime]=mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime|None]=mapped_column(DateTime(timezone=True))
    error_details: Mapped[str|None]=mapped_column(Text)

class IntegrationError(Identity,Base):
    __tablename__='integration_errors'
    connection_id: Mapped[uuid.UUID]=mapped_column(ForeignKey('integration_connections.id'),index=True)
    run_id: Mapped[uuid.UUID|None]=mapped_column(ForeignKey('integration_sync_runs.id'))
    operation: Mapped[str]=mapped_column(String(40))
    error_code: Mapped[str]=mapped_column(String(40))
    message: Mapped[str]=mapped_column(Text)
