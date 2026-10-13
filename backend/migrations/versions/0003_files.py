"""Register the bounded secure CSV connector."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
import uuid
revision='0003_files'
down_revision='0002_integrations'
branch_labels=None
depends_on=None

def upgrade():
    table=sa.table('external_applications',sa.column('id',sa.UUID()),sa.column('slug',sa.String()),sa.column('name',sa.String()),sa.column('description',sa.Text()),sa.column('authentication_methods',postgresql.JSONB()))
    op.bulk_insert(table,[{'id':uuid.uuid5(uuid.NAMESPACE_URL,'reconcile:csv'),'slug':'csv','name':'Secure CSV Import','description':'Validated UTF-8 CSV uploads are ingested through Data Ingestion.','authentication_methods':[]}])

def downgrade():
    op.execute(sa.text("DELETE FROM external_applications WHERE slug = 'csv'"))
