"""Alembic environment. Runs against a connection supplied by agentscope.storage.db."""

from __future__ import annotations

from alembic import context
from sqlalchemy import Connection, create_engine

from agentscope.storage.models import Base

target_metadata = Base.metadata


def run_migrations(connection: Connection) -> None:
    is_sqlite = connection.dialect.name == "sqlite"
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        render_as_batch=is_sqlite,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


connection = context.config.attributes.get("connection")
if connection is not None:
    run_migrations(connection)
else:  # invoked through the alembic CLI with sqlalchemy.url set
    url = context.config.get_main_option("sqlalchemy.url")
    assert url, "set sqlalchemy.url or use `agentscope db upgrade`"
    with create_engine(url).begin() as conn:
        run_migrations(conn)
