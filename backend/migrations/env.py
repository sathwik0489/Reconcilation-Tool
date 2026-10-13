from alembic import context
from app.core.database import engine
from app.models import Base

if context.is_offline_mode():
    from app.core.config import get_settings
    context.configure(url=get_settings().database_url, target_metadata=Base.metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()
else:
    def run(connection):
        context.configure(connection=connection, target_metadata=Base.metadata, compare_type=True)
        with context.begin_transaction():
            context.run_migrations()
    if context.config.attributes.get('connection') is not None:
        run(context.config.attributes['connection'])
    else:
        with engine.connect() as connection:
            run(connection)
