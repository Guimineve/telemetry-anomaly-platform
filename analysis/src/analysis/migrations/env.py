from alembic import context

from analysis.db import Base

config = context.config
connection = config.attributes.get("connection")
if connection is None:
    raise RuntimeError("migrations are run through analysis.migrate.run_migrations")

context.configure(connection=connection, target_metadata=Base.metadata)
with context.begin_transaction():
    context.run_migrations()
