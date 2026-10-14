"""Organizations, memberships, and organization scoping for workspace metadata."""
from alembic import op
import sqlalchemy as sa
import uuid
revision='0004_organizations'
down_revision='0003_files'
branch_labels=None
depends_on=None

SCOPED=['database_connections','registered_tables','ingestion_configurations','ingestion_runs','table_mappings',
        'reconciliation_jobs','reconciliation_runs','integration_connections']
DEFAULT_ORGANIZATION=uuid.uuid5(uuid.NAMESPACE_URL,'reconcile:default-organization')

def timestamps():
    return [sa.Column('id',sa.UUID(),nullable=False),
        sa.Column('created_at',sa.DateTime(timezone=True),server_default=sa.text('now()'),nullable=False),
        sa.Column('updated_at',sa.DateTime(timezone=True),server_default=sa.text('now()'),nullable=False)]

def upgrade():
    op.create_table('organizations',sa.Column('name',sa.String(length=100),nullable=False),*timestamps(),sa.PrimaryKeyConstraint('id'))
    op.create_table('organization_memberships',
        sa.Column('organization_id',sa.UUID(),nullable=False),sa.Column('user_id',sa.UUID(),nullable=False),
        sa.Column('role',sa.String(length=20),nullable=False),*timestamps(),
        sa.CheckConstraint("role IN ('admin','engineer','analyst','viewer')",name='membership_role'),
        sa.ForeignKeyConstraint(['organization_id'],['organizations.id'],ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id'],['users.id'],ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),sa.UniqueConstraint('organization_id','user_id',name='unique_membership'))
    op.create_index(op.f('ix_organization_memberships_organization_id'),'organization_memberships',['organization_id'],unique=False)
    op.create_index(op.f('ix_organization_memberships_user_id'),'organization_memberships',['user_id'],unique=False)
    connection=op.get_bind()
    # Existing single-workspace installations keep everything, inside one default organization.
    existing=connection.execute(sa.text('SELECT count(*) FROM users')).scalar()
    if existing:
        connection.execute(sa.text("INSERT INTO organizations (id,name) VALUES (:id,'Default organization')"),{'id':DEFAULT_ORGANIZATION})
        for user_id,role in connection.execute(sa.text('SELECT id,role FROM users')).all():
            connection.execute(sa.text('INSERT INTO organization_memberships (id,organization_id,user_id,role) VALUES (:id,:organization,:user,:role)'),
                {'id':uuid.uuid4(),'organization':DEFAULT_ORGANIZATION,'user':user_id,'role':role})
    for table in SCOPED+['audit_logs']:
        op.add_column(table,sa.Column('organization_id',sa.UUID(),nullable=True))
        if existing:
            # Sign-in failures have no actor and stay unscoped.
            condition=' WHERE actor_id IS NOT NULL' if table=='audit_logs' else ''
            connection.execute(sa.text(f'UPDATE {table} SET organization_id = :id{condition}'),{'id':DEFAULT_ORGANIZATION})
        if table!='audit_logs':op.alter_column(table,'organization_id',nullable=False)
        op.create_foreign_key(f'{table}_organization_id_fkey',table,'organizations',['organization_id'],['id'])
        op.create_index(op.f(f'ix_{table}_organization_id'),table,['organization_id'],unique=False)
    op.drop_constraint('database_connections_name_key','database_connections',type_='unique')
    op.create_unique_constraint('unique_connection_name','database_connections',['organization_id','name'])
    op.drop_constraint('integration_connections_owner_id_name_key','integration_connections',type_='unique')
    op.create_unique_constraint('unique_integration_name','integration_connections',['organization_id','name'])
    op.drop_constraint('user_role','users',type_='check')
    op.drop_column('users','role')

def downgrade():
    op.add_column('users',sa.Column('role',sa.String(length=20),nullable=True))
    connection=op.get_bind()
    # A user keeps the role of their earliest membership; analysts become viewers in the three-role model.
    connection.execute(sa.text("""UPDATE users SET role = CASE WHEN m.role = 'analyst' THEN 'viewer' ELSE m.role END
        FROM (SELECT DISTINCT ON (user_id) user_id, role FROM organization_memberships ORDER BY user_id, created_at, id) m
        WHERE m.user_id = users.id"""))
    connection.execute(sa.text("UPDATE users SET role = 'viewer' WHERE role IS NULL"))
    op.alter_column('users','role',nullable=False)
    op.create_check_constraint('user_role','users',"role IN ('admin','engineer','viewer')")
    op.drop_constraint('unique_integration_name','integration_connections',type_='unique')
    op.create_unique_constraint('integration_connections_owner_id_name_key','integration_connections',['owner_id','name'])
    op.drop_constraint('unique_connection_name','database_connections',type_='unique')
    op.create_unique_constraint('database_connections_name_key','database_connections',['name'])
    for table in SCOPED+['audit_logs']:
        op.drop_index(op.f(f'ix_{table}_organization_id'),table_name=table)
        op.drop_column(table,'organization_id')
    op.drop_index(op.f('ix_organization_memberships_user_id'),table_name='organization_memberships')
    op.drop_index(op.f('ix_organization_memberships_organization_id'),table_name='organization_memberships')
    op.drop_table('organization_memberships')
    op.drop_table('organizations')
