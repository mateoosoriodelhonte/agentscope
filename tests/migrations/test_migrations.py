"""Migrations apply cleanly and match the ORM models, on SQLite and (when configured) PostgreSQL."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from sqlalchemy import Engine, inspect, text

from agentscope.storage.db import create_db_engine, current_revision, head_revision, upgrade
from agentscope.storage.models import Base


def _assert_no_drift(engine: Engine) -> None:
    with engine.connect() as connection:
        context = MigrationContext.configure(connection, opts={"compare_type": True})
        diff = compare_metadata(context, Base.metadata)
    assert diff == [], f"models and migrations differ: {diff}"


def _exercise(engine: Engine) -> None:
    assert current_revision(engine) is None
    assert upgrade(engine) == head_revision()
    assert upgrade(engine) == head_revision()  # idempotent fast path
    tables = set(inspect(engine).get_table_names())
    assert {"runs", "spans", "alembic_version"} <= tables
    _assert_no_drift(engine)


def test_sqlite_upgrade_and_drift(tmp_path: Path) -> None:
    engine = create_db_engine(f"sqlite:///{tmp_path / 'm.db'}")
    _exercise(engine)
    with engine.connect() as connection:
        assert connection.execute(text("PRAGMA foreign_keys")).scalar() == 1
        assert connection.execute(text("PRAGMA journal_mode")).scalar() == "wal"
    mode = (tmp_path / "m.db").stat().st_mode & 0o777
    assert mode == 0o600


@pytest.mark.postgres
def test_postgres_upgrade_and_drift() -> None:
    url = os.environ.get("AGENTSCOPE_TEST_POSTGRES_URL")
    if not url:
        pytest.skip("AGENTSCOPE_TEST_POSTGRES_URL not set")
    engine = create_db_engine(url)
    with engine.begin() as connection:
        connection.execute(text("DROP SCHEMA public CASCADE"))
        connection.execute(text("CREATE SCHEMA public"))
    _exercise(engine)
