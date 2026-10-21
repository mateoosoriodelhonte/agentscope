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
from sqlalchemy import (
    Column,
    Engine,
    MetaData,
    Table,
    bindparam,
    func,
    insert,
    inspect,
    select,
    text,
)

from agentscope.domain.agents import AgentConfig, Limits
from agentscope.domain.traces import RunStatus, SpanKind, SpanRecord, TraceRecord
from agentscope.storage.db import (
    DatabaseAheadError,
    alembic_config,
    create_db_engine,
    current_revision,
    head_revision,
    known_revision,
    migration_connection,
    upgrade,
)
from agentscope.storage.models import Base, RunRow, SpanRow
from agentscope.storage.repository import Repository
from agentscope.storage.types import UTCDateTime

TABLES_0002 = {"datasets", "suite_runs", "case_results", "check_results", "experiments"}
LIVENESS_0003 = {"owner", "heartbeat_at"}
TABLES_0004 = {"ablations"}


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


def _save_with_old_schema(engine: Engine, trace: TraceRecord, suite_id: uuid.UUID) -> None:
    """Store `trace` in a database at an older revision than the ORM models: it is written at
    head into a scratch database, then copied with only the columns the old tables have."""
    scratch = create_db_engine("sqlite://")
    upgrade(scratch)
    Repository(scratch).save_trace(trace)
    with scratch.connect() as source, engine.begin() as target:
        for model in (RunRow, SpanRow):
            present = {c["name"] for c in inspect(target).get_columns(model.__tablename__)}
            columns = [c for c in model.__table__.columns if c.name in present]
            table = Table(
                model.__tablename__, MetaData(), *(Column(c.name, c.type) for c in columns)
            )
            rows = [dict(row._mapping) for row in source.execute(select(*columns))]
            if model is RunRow:
                rows = [{**row, "suite_id": suite_id} for row in rows]
            target.execute(insert(table), rows)
    scratch.dispose()


def test_upgrade_and_drift(engine: Engine) -> None:
    assert current_revision(engine) is None
    assert upgrade(engine) == head_revision()
    assert upgrade(engine) == head_revision()  # idempotent fast path
    tables = set(inspect(engine).get_table_names())
    assert {"runs", "spans", "alembic_version"} | TABLES_0002 | TABLES_0004 <= tables
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
    _save_with_old_schema(engine, trace, suite_id=uuid.uuid4())

    upgrade(engine)

    with engine.connect() as connection:
        assert connection.scalar(select(func.count()).select_from(RunRow)) == 1
        assert connection.scalar(select(func.count()).select_from(SpanRow)) == 1
        assert connection.scalar(select(RunRow.suite_id)) is None
    stored = repository.get_trace(trace.run_id)
    assert stored is not None
    assert len(stored.spans) == 1

    # Downgrading rebuilds `runs` again on SQLite; the spans must survive that too.
    _migrate(engine, command.downgrade, "0001")
    with engine.connect() as connection:
        assert connection.scalar(select(func.count()).select_from(SpanRow)) == 1


def test_failed_sqlite_migration_leaves_no_partial_schema(tmp_path: Path) -> None:
    """pysqlite autocommits DDL unless a transaction is opened explicitly."""
    engine = create_db_engine(f"sqlite:///{tmp_path / 'm.db'}")

    def half_a_migration() -> None:
        with migration_connection(engine) as connection:
            connection.exec_driver_sql("CREATE TABLE half_done (id INTEGER)")
            raise RuntimeError("boom")

    with pytest.raises(RuntimeError, match="boom"):
        half_a_migration()
    assert "half_done" not in inspect(engine).get_table_names()
    with engine.connect() as connection:
        assert connection.execute(text("PRAGMA foreign_keys")).scalar() == 1
    engine.dispose()


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


def _columns(engine: Engine, table: str) -> set[str]:
    return {column["name"] for column in inspect(engine).get_columns(table)}


def _insert_old_suite(engine: Engine) -> None:
    """A suite row with only the columns every revision since 0002 has. The ORM models
    already have later columns, so it is written with SQL."""
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO suite_runs (id, label, status, dataset_name, dataset_version, "
                "dataset_hash, agent_ref, config_hash, effective_config_hash, provider, model, "
                "deterministic, agent_config, checks_enabled, environment, total, passed, "
                "pass_rate, started_at) VALUES (:id, '', 'running', 'geo', '1', 'h', 'a@v1', "
                "'c', 'e', 'fake', 'm', :true, '{}', '[]', '{}', 0, 0, 0, :now)"
            ).bindparams(bindparam("now", type_=UTCDateTime())),
            {"id": uuid.uuid4().hex, "true": True, "now": datetime.now(UTC)},
        )


def test_0003_adds_owner_and_heartbeat_and_keeps_data(engine: Engine) -> None:
    _migrate(engine, command.upgrade, "0002")
    _insert_old_suite(engine)
    for table in ("suite_runs", "runs"):
        assert not _columns(engine, table) & LIVENESS_0003

    upgrade(engine)
    for table in ("suite_runs", "runs"):
        assert _columns(engine, table) >= LIVENESS_0003
    with engine.connect() as connection:
        assert connection.execute(text("SELECT count(*), max(owner) FROM suite_runs")).one() == (
            1,
            None,
        )
    _assert_no_drift(engine)

    _migrate(engine, command.downgrade, "0002")
    for table in ("suite_runs", "runs"):
        assert not _columns(engine, table) & LIVENESS_0003
    with engine.connect() as connection:
        assert connection.execute(text("SELECT count(*) FROM suite_runs")).scalar() == 1


def test_0004_adds_ablations_and_keeps_suites(engine: Engine) -> None:
    _migrate(engine, command.upgrade, "0003")
    _insert_old_suite(engine)
    assert not set(inspect(engine).get_table_names()) & TABLES_0004
    assert "ablation_id" not in _columns(engine, "suite_runs")

    upgrade(engine)
    assert set(inspect(engine).get_table_names()) >= TABLES_0004
    assert "ablation_id" in _columns(engine, "suite_runs")
    indexed = {index["name"] for index in inspect(engine).get_indexes("suite_runs")}
    assert "ix_suite_runs_ablation_id" in indexed
    with engine.connect() as connection:
        assert connection.execute(
            text("SELECT count(*), count(ablation_id) FROM suite_runs")
        ).one() == (1, 0)
    _assert_no_drift(engine)

    _migrate(engine, command.downgrade, "0003")
    assert not set(inspect(engine).get_table_names()) & TABLES_0004
    assert "ablation_id" not in _columns(engine, "suite_runs")
    with engine.connect() as connection:
        assert connection.execute(text("SELECT count(*) FROM suite_runs")).scalar() == 1
    assert upgrade(engine) == head_revision()
    _assert_no_drift(engine)


def test_a_database_from_a_newer_release_is_refused(engine: Engine) -> None:
    """Alembic raises for an unknown revision instead of returning None."""
    upgrade(engine)
    with engine.begin() as connection:
        connection.execute(text("UPDATE alembic_version SET version_num = '9999'"))
    assert not known_revision("9999")
    assert known_revision(head_revision())
    with pytest.raises(DatabaseAheadError, match="9999 is unknown"):
        upgrade(engine)


def test_an_existing_directory_is_left_as_it_is(tmp_path: Path) -> None:
    """A database pointed into a project directory must not chmod it or make git ignore it."""
    project = tmp_path / "project"
    project.mkdir(mode=0o755)
    project.chmod(0o755)
    engine = create_db_engine(f"sqlite:///{project / 'agentscope.db'}")
    upgrade(engine)
    engine.dispose()
    assert project.stat().st_mode & 0o777 == 0o755
    assert not (project / ".gitignore").exists()
    assert (project / "agentscope.db").stat().st_mode & 0o777 == 0o600


def test_a_directory_agentscope_creates_is_private(tmp_path: Path) -> None:
    data = tmp_path / "fresh" / "data"
    engine = create_db_engine(f"sqlite:///{data / 'agentscope.db'}")
    upgrade(engine)
    engine.dispose()
    assert data.stat().st_mode & 0o777 == 0o700
    assert (data / ".gitignore").read_text(encoding="utf-8") == "*\n"
    for suffix in ("", "-wal", "-shm"):
        path = data / f"agentscope.db{suffix}"
        if path.exists():
            assert path.stat().st_mode & 0o777 == 0o600, path
