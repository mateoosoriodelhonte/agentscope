"""Engine creation, SQLite hardening, and programmatic migrations."""

from __future__ import annotations

import os
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from importlib import resources
from pathlib import Path
from typing import Any

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import Connection, Engine, create_engine, event
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import ConnectionPoolEntry


class DatabaseAheadError(RuntimeError):
    """The database was migrated by a newer AgentScope than the one running."""


class MigrationIntegrityError(RuntimeError):
    """A migration left rows that violate a foreign key; it was rolled back."""


def normalize_url(url: str) -> str:
    """Use the psycopg 3 driver for plain postgresql:// URLs."""
    if url.startswith("postgresql://"):
        return "postgresql+psycopg://" + url.removeprefix("postgresql://")
    if url.startswith("postgres://"):
        return "postgresql+psycopg://" + url.removeprefix("postgres://")
    return url


def _prepare_sqlite_file(url: str) -> None:
    database = make_url(url).database
    if not database or database == ":memory:":
        return
    path = Path(database)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Local traces can contain task text and tool payloads: owner-only permissions.
    os.chmod(path.parent, stat.S_IRWXU)
    ignore = path.parent / ".gitignore"
    if not ignore.exists():
        ignore.write_text("*\n", encoding="utf-8")
    if not path.exists():
        path.touch(mode=0o600)


def _sqlite_pragmas(dbapi_connection: Any, _record: ConnectionPoolEntry) -> None:
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA busy_timeout=5000")
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA secure_delete=ON")  # purged rows are overwritten, not just unlinked
    cursor.close()


def create_db_engine(url: str) -> Engine:
    url = normalize_url(url)
    if url.startswith("sqlite"):
        _prepare_sqlite_file(url)
        engine = create_engine(url, connect_args={"check_same_thread": False})
        event.listen(engine, "connect", _sqlite_pragmas)
        return engine
    return create_engine(url, pool_pre_ping=True)


def session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(engine, expire_on_commit=False)


def alembic_config() -> Config:
    config = Config()
    config.set_main_option(
        "script_location", str(resources.files("agentscope.storage").joinpath("migrations"))
    )
    return config


def head_revision() -> str:
    head = ScriptDirectory.from_config(alembic_config()).get_current_head()
    assert head is not None
    return head


def current_revision(engine: Engine) -> str | None:
    with engine.connect() as connection:
        return MigrationContext.configure(connection).get_current_revision()


def upgrade(engine: Engine) -> str:
    """Bring the schema to head. Returns the resulting revision."""
    script = ScriptDirectory.from_config(alembic_config())
    current = current_revision(engine)
    head = head_revision()
    if current == head:
        return head
    if current is not None and script.get_revision(current) is None:
        raise DatabaseAheadError(
            f"database revision {current} is unknown to this AgentScope version; upgrade AgentScope"
        )
    config = alembic_config()
    with migration_connection(engine) as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "head")
    return head


@contextmanager
def migration_connection(engine: Engine) -> Iterator[Connection]:
    """A connection in a transaction that Alembic can safely run on.

    SQLite batch migrations rebuild a table (copy, drop, rename). With foreign keys enforced,
    dropping a parent table deletes its rows first and cascades to every child, so
    enforcement is off while migrating, all constraints are re-checked before commit, and
    enforcement is restored before the connection returns to the pool. The PRAGMA is ignored
    inside a transaction, hence the commits around it. PostgreSQL serializes concurrent
    upgrades with an advisory lock instead.
    """
    with engine.connect() as connection:
        sqlite = connection.dialect.name == "sqlite"
        if sqlite:
            connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
            connection.commit()
        try:
            with connection.begin():
                if connection.dialect.name == "postgresql":
                    connection.exec_driver_sql("SELECT pg_advisory_xact_lock(7426010)")
                yield connection
                if sqlite:
                    violations = connection.exec_driver_sql("PRAGMA foreign_key_check").all()
                    if violations:
                        raise MigrationIntegrityError(
                            f"foreign key violations after migrating: {violations[:5]}"
                        )
        finally:
            if sqlite:
                connection.exec_driver_sql("PRAGMA foreign_keys=ON")
                connection.commit()
