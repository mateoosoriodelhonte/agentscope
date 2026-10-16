"""Migrations apply cleanly, round-trip, keep existing data, and match the ORM models, on
SQLite and (when configured) PostgreSQL."""

from __future__ import annotations

import os
import uuid
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from sqlalchemy import Engine, func, inspect, select, text

from agentscope.domain.agents import AgentConfig, Limits
from agentscope.domain.traces import RunStatus, SpanKind, SpanRecord, TraceRecord
from agentscope.storage.db import (
    alembic_config,
    create_db_engine,
    current_revision,
    head_revision,
    migration_connection,
    upgrade,
)
from agentscope.storage.models import Base, RunRow, SpanRow
from agentscope.storage.repository import Repository

TABLES_0002 = {"datasets", "suite_runs", "case_results", "check_results", "experiments"}


@pytest.fixture(params=["sqlite", pytest.param("postgresql", marks=pytest.mark.postgres)])
def engine(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[Engine]:
    if request.param == "sqlite":
        url = f"sqlite:///{tmp_path / 'm.db'}"
    else:
        url = os.environ.get("AGENTSCOPE_TEST_POSTGRES_URL", "")
        if not url:
            pytest.skip("AGENTSCOPE_TEST_POSTGRES_URL not set")
    engine = create_db_engine(url)
    if request.param == "postgresql":
        with engine.begin() as connection:
            connection.execute(text("DROP SCHEMA public CASCADE"))
            connection.execute(text("CREATE SCHEMA public"))
    yield engine
    engine.dispose()


def _migrate(engine: Engine, action: Callable[[Config, str], None], revision: str) -> None:
    config = alembic_config()
    with migration_connection(engine) as connection:
        config.attributes["connection"] = connection
        action(config, revision)


def _assert_no_drift(engine: Engine) -> None:
    with engine.connect() as connection:
        context = MigrationContext.configure(connection, opts={"compare_type": True})
        diff = compare_metadata(context, Base.metadata)
    assert diff == [], f"models and migrations differ: {diff}"


def _trace() -> TraceRecord:
    now = datetime.now(UTC)
    trace_id = uuid.uuid4().hex
    config = AgentConfig(agent="research_assistant", version="v1", prompt_version="v1", tools=[])
    return TraceRecord(
        trace_id=trace_id,
        run_id=str(uuid.uuid4()),
        agent=config.agent,
        agent_version=config.version,
        config_hash=config.config_hash(),
        effective_config_hash=config.config_hash(),
        config=config,
        limits=Limits(),
        permitted_tools=[],
        task="What year?",
        status=RunStatus.COMPLETED,
        started_at=now,
        ended_at=now,
        duration_ms=1.0,
        steps=1,
        provider="fake",
        model="scripted-careful",
        spans=[
            SpanRecord(
                trace_id=trace_id,
                span_id=uuid.uuid4().hex[:16],
                name="invoke_agent research_assistant",
                kind=SpanKind.RUN,
                start_time=now,
                end_time=now,
                duration_ms=1.0,
            )
        ],
    )


def test_upgrade_and_drift(engine: Engine) -> None:
    assert current_revision(engine) is None
    assert upgrade(engine) == head_revision()
    assert upgrade(engine) == head_revision()  # idempotent fast path
    tables = set(inspect(engine).get_table_names())
    assert {"runs", "spans", "alembic_version"} | TABLES_0002 <= tables
    _assert_no_drift(engine)


def test_downgrade_to_0001_and_upgrade_again(engine: Engine) -> None:
    upgrade(engine)
    _migrate(engine, command.downgrade, "0001")
    assert current_revision(engine) == "0001"
    assert not set(inspect(engine).get_table_names()) & TABLES_0002
    assert inspect(engine).get_foreign_keys("runs") == []
    assert upgrade(engine) == head_revision()
    _assert_no_drift(engine)


def test_upgrade_keeps_runs_and_spans(engine: Engine) -> None:
    """SQLite rebuilds `runs` to add its foreign key; that must not cascade to `spans`."""
    _migrate(engine, command.upgrade, "0001")
    repository = Repository(engine)
    trace = _trace()
    # 0001 has no suite_runs table, so a stored suite_id can only be dangling.
    repository.save_trace(trace, suite_id=str(uuid.uuid4()))

    upgrade(engine)

    with engine.connect() as connection:
        assert connection.scalar(select(func.count()).select_from(RunRow)) == 1
        assert connection.scalar(select(func.count()).select_from(SpanRow)) == 1
        assert connection.scalar(select(RunRow.suite_id)) is None
    stored = repository.get_trace(trace.run_id)
    assert stored is not None
    assert len(stored.spans) == 1


def test_sqlite_connection_settings_survive_migrations(tmp_path: Path) -> None:
    engine = create_db_engine(f"sqlite:///{tmp_path / 'm.db'}")
    upgrade(engine)
    # The migration connection goes back to the pool: enforcement must be on again.
    with engine.connect() as connection:
        assert connection.execute(text("PRAGMA foreign_keys")).scalar() == 1
        assert connection.execute(text("PRAGMA journal_mode")).scalar() == "wal"
        assert connection.execute(text("PRAGMA secure_delete")).scalar() == 1
    mode = (tmp_path / "m.db").stat().st_mode & 0o777
    assert mode == 0o600
    engine.dispose()
