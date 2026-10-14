"""Metadata only: customer datasets are never stored in this database."""
import uuid
from datetime import datetime
from sqlalchemy import (String, Text, Integer, BigInteger, Boolean, DateTime, Numeric,
                        ForeignKey, UniqueConstraint, CheckConstraint, func)
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

class Base(DeclarativeBase):
    pass

class Identity:
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

class Organization(Identity, Base):
    __tablename__ = 'organizations'
    name: Mapped[str] = mapped_column(String(100))

class OrgScoped:
    """Rows that belong to exactly one organization; every query must filter on it."""
    organization_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('organizations.id'), index=True)

class User(Identity, Base):
    __tablename__ = 'users'
    email: Mapped[str] = mapped_column(String(254), unique=True)
    name: Mapped[str] = mapped_column(String(100))
    password_hash: Mapped[str] = mapped_column(Text)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    token_version: Mapped[int] = mapped_column(Integer, default=0)
    preferences: Mapped[dict] = mapped_column(JSONB, default=dict)
    # Not columns: the session's organization and the role held there, set per request by app.core.security.
    role = None
    organization_id = None
    organization_name = None

class OrganizationMembership(Identity, Base):
    __tablename__ = 'organization_memberships'
    organization_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('organizations.id', ondelete='CASCADE'), index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('users.id', ondelete='CASCADE'), index=True)
    role: Mapped[str] = mapped_column(String(20), default='viewer')
    __table_args__ = (UniqueConstraint('organization_id','user_id', name='unique_membership'),
                     CheckConstraint("role IN ('admin','engineer','analyst','viewer')", name='membership_role'))

class DatabaseConnection(OrgScoped, Identity, Base):
    __tablename__ = 'database_connections'
    name: Mapped[str] = mapped_column(String(100))
    connection_type: Mapped[str] = mapped_column(String(30))
    classification: Mapped[str] = mapped_column(String(10))
    hostname: Mapped[str | None] = mapped_column(String(255))
    port: Mapped[int | None] = mapped_column(Integer)
    database_name: Mapped[str | None] = mapped_column(String(128))
    schema_name: Mapped[str | None] = mapped_column(String(128))
    authentication_method: Mapped[str] = mapped_column(String(40), default='password')
    username: Mapped[str | None] = mapped_column(String(128))
    encrypted_credential: Mapped[str | None] = mapped_column(Text)
    credential_reference: Mapped[str | None] = mapped_column(String(255))
    description: Mapped[str | None] = mapped_column(Text)
    owner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('users.id'))
    __table_args__ = (UniqueConstraint('organization_id','name', name='unique_connection_name'),
                     CheckConstraint("classification IN ('source','target')", name='connection_classification'),
                     CheckConstraint('port IS NULL OR (port > 0 AND port < 65536)', name='connection_port'))

class MedallionLayer(Identity, Base):
    __tablename__ = 'medallion_layers'
    name: Mapped[str] = mapped_column(String(10), unique=True)
    description: Mapped[str] = mapped_column(Text)
    ordinal: Mapped[int] = mapped_column(Integer, unique=True)
    __table_args__ = (CheckConstraint("name IN ('bronze','silver','gold')", name='layer_name'),)

class RegisteredTable(OrgScoped, Identity, Base):
    __tablename__ = 'registered_tables'
    connection_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('database_connections.id'), index=True)
    database_name: Mapped[str] = mapped_column(String(128))
    schema_name: Mapped[str] = mapped_column(String(128))
    table_name: Mapped[str] = mapped_column(String(128))
    description: Mapped[str | None] = mapped_column(Text)
    classification: Mapped[str] = mapped_column(String(10))
    layer_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey('medallion_layers.id'), index=True)
    __table_args__ = (UniqueConstraint('connection_id','database_name','schema_name','table_name', name='unique_registered_table'),
                     CheckConstraint("classification IN ('source','target')", name='table_classification'))

class TableColumn(Identity, Base):
    __tablename__ = 'table_columns'
    table_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('registered_tables.id', ondelete='CASCADE'), index=True)
    name: Mapped[str] = mapped_column(String(128))
    data_type: Mapped[str] = mapped_column(String(100))
    ordinal: Mapped[int] = mapped_column(Integer)
    primary_key: Mapped[bool] = mapped_column(Boolean, default=False)
    nullable: Mapped[bool] = mapped_column(Boolean, default=True)
    default_value: Mapped[str | None] = mapped_column(Text)
    __table_args__ = (UniqueConstraint('table_id','name'), UniqueConstraint('table_id','ordinal'))

class IngestionConfiguration(OrgScoped, Identity, Base):
    __tablename__ = 'ingestion_configurations'
    name: Mapped[str] = mapped_column(String(100))
    source_connection_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('database_connections.id'))
    destination_connection_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('database_connections.id'))
    source_table_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey('registered_tables.id'))
    destination_table_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('registered_tables.id'))
    ingestion_type: Mapped[str] = mapped_column(String(20))
    mode: Mapped[str] = mapped_column(String(20), default='full')
    watermark_column: Mapped[str | None] = mapped_column(String(128))
    watermark_value: Mapped[str | None] = mapped_column(Text)
    options: Mapped[dict] = mapped_column(JSONB, default=dict)
    owner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('users.id'))
    __table_args__ = (CheckConstraint("mode IN ('full','incremental')", name='ingestion_mode'),)

class IngestionRun(OrgScoped, Identity, Base):
    __tablename__ = 'ingestion_runs'
    configuration_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('ingestion_configurations.id'), index=True)
    status: Mapped[str] = mapped_column(String(20), default='queued')
    record_count: Mapped[int] = mapped_column(BigInteger, default=0)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_details: Mapped[str | None] = mapped_column(Text)
    executed_by: Mapped[uuid.UUID] = mapped_column(ForeignKey('users.id'))
    __table_args__ = (CheckConstraint("status IN ('queued','running','succeeded','failed')", name='ingestion_status'),
                     CheckConstraint('record_count >= 0', name='ingestion_count'))

class TableMapping(OrgScoped, Identity, Base):
    __tablename__ = 'table_mappings'
    name: Mapped[str] = mapped_column(String(100))
    mapping_type: Mapped[str] = mapped_column(String(20))
    owner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('users.id'))
    description: Mapped[str | None] = mapped_column(Text)
    version: Mapped[int] = mapped_column(Integer, default=1)
    __table_args__ = (CheckConstraint("mapping_type IN ('one_to_one','one_to_many','many_to_one')", name='mapping_type'),)

class MappingEdge(Identity, Base):
    """An independent target branch; normalized memberships support fan-in and fan-out."""
    __tablename__ = 'mapping_edges'
    mapping_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('table_mappings.id', ondelete='CASCADE'), index=True)
    target_table_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('registered_tables.id'))
    business_keys: Mapped[list] = mapped_column(JSONB, default=list)
    join_conditions: Mapped[list] = mapped_column(JSONB, default=list)
    filters: Mapped[dict] = mapped_column(JSONB, default=dict)
    aggregations: Mapped[list] = mapped_column(JSONB, default=list)
    __table_args__ = (UniqueConstraint('mapping_id','target_table_id'),)

class MappingSource(Identity, Base):
    __tablename__ = 'mapping_sources'
    edge_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('mapping_edges.id', ondelete='CASCADE'), index=True)
    source_table_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('registered_tables.id'))
    __table_args__ = (UniqueConstraint('edge_id','source_table_id'),)

class ColumnMapping(Identity, Base):
    __tablename__ = 'column_mappings'
    edge_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('mapping_edges.id', ondelete='CASCADE'), index=True)
    source_column_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('table_columns.id'))
    target_column_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('table_columns.id'))
    transformation: Mapped[dict] = mapped_column(JSONB, default=dict)
    __table_args__ = (UniqueConstraint('edge_id','source_column_id','target_column_id'),)

class ReconciliationJob(OrgScoped, Identity, Base):
    __tablename__ = 'reconciliation_jobs'
    name: Mapped[str] = mapped_column(String(100))
    mapping_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('table_mappings.id'))
    owner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('users.id'))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)

class ReconciliationRule(Identity, Base):
    __tablename__ = 'reconciliation_rules'
    job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('reconciliation_jobs.id', ondelete='CASCADE'), index=True)
    edge_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey('mapping_edges.id'))
    rule_type: Mapped[str] = mapped_column(String(40))
    configuration: Mapped[dict] = mapped_column(JSONB, default=dict)

class ReconciliationRun(OrgScoped, Identity, Base):
    __tablename__ = 'reconciliation_runs'
    job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('reconciliation_jobs.id'), index=True)
    executed_by: Mapped[uuid.UUID] = mapped_column(ForeignKey('users.id'))
    status: Mapped[str] = mapped_column(String(20), default='queued')
    configuration_snapshot: Mapped[dict] = mapped_column(JSONB, default=dict)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_ms: Mapped[int | None] = mapped_column(BigInteger)
    error_details: Mapped[str | None] = mapped_column(Text)
    __table_args__ = (CheckConstraint("status IN ('queued','running','succeeded','failed')", name='reconciliation_status'),)

class ReconciliationResult(Identity, Base):
    __tablename__ = 'reconciliation_results'
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('reconciliation_runs.id', ondelete='CASCADE'), index=True)
    edge_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('mapping_edges.id'))
    source_count: Mapped[int] = mapped_column(BigInteger)
    target_count: Mapped[int] = mapped_column(BigInteger)
    matched: Mapped[int] = mapped_column(BigInteger)
    mismatched: Mapped[int] = mapped_column(BigInteger)
    missing: Mapped[int] = mapped_column(BigInteger)
    extra: Mapped[int] = mapped_column(BigInteger)
    duplicates: Mapped[int] = mapped_column(BigInteger)
    match_percentage: Mapped[float | None] = mapped_column(Numeric(7,4))
    rule_results: Mapped[list] = mapped_column(JSONB, default=list)
    __table_args__ = (UniqueConstraint('run_id','edge_id'), CheckConstraint('source_count >= 0 AND target_count >= 0 AND matched >= 0 AND mismatched >= 0 AND missing >= 0 AND extra >= 0 AND duplicates >= 0', name='result_counts'), CheckConstraint('match_percentage IS NULL OR (match_percentage >= 0 AND match_percentage <= 100)', name='match_percentage'))

class ReconciliationDifference(Identity, Base):
    __tablename__ = 'reconciliation_differences'
    result_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('reconciliation_results.id', ondelete='CASCADE'), index=True)
    business_key: Mapped[dict] = mapped_column(JSONB)
    difference_type: Mapped[str] = mapped_column(String(30))
    column_name: Mapped[str | None] = mapped_column(String(128))
    source_value: Mapped[dict | None] = mapped_column(JSONB)
    target_value: Mapped[dict | None] = mapped_column(JSONB)

class AuditLog(Identity, Base):
    __tablename__ = 'audit_logs'
    # Null only for events with no signed-in organization, such as a failed sign-in.
    organization_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey('organizations.id'), index=True)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey('users.id'), index=True)
    action: Mapped[str] = mapped_column(String(100), index=True)
    resource_type: Mapped[str] = mapped_column(String(60))
    resource_id: Mapped[str | None] = mapped_column(String(100))
    details: Mapped[dict] = mapped_column(JSONB, default=dict)
