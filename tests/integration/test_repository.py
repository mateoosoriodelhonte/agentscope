"""Repository round trips, conflicts, crash recovery, inventory and purge, on SQLite and (when
configured) PostgreSQL."""

from __future__ import annotations

import os
import sqlite3
import uuid
from collections.abc import Iterator, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import event, text

from agentscope.domain.agents import AgentConfig
from agentscope.domain.datasets import Dataset, EvalCase, ExpectedAnswer
from agentscope.domain.evaluation import (
    CHECK_ENGINE_VERSION,
    CaseResult,
    CheckName,
    CheckResult,
    CheckStats,
    CheckStatus,
    EnvironmentFingerprint,
    GateResult,
    GateRule,
    ReasonCode,
    SuiteMetrics,
    SuiteResult,
)
from agentscope.domain.experiments import (
    CaseChange,
    CaseComparison,
    CheckFlip,
    Comparison,
    ConfigDifference,
    MetricDelta,
    SuiteSummary,
)
from agentscope.domain.tools import FaultKind, FaultSpec, ModelFaultKind, ModelFaultSpec
from agentscope.domain.traces import RunStatus, SpanKind, SpanRecord, SpanStatus, TraceRecord
from agentscope.storage.db import create_db_engine, upgrade
from agentscope.storage.models import SuiteRunRow
from agentscope.storage.repository import (
    DatasetConflict,
    EvaluatedCase,
    PurgeIncomplete,
    Repository,
    SuiteHeader,
)
from agentscope.tracing.redaction import Redactor

NOW = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
SECRET = "configured-secret-value-1234"
ENVIRONMENT = EnvironmentFingerprint(
    agentscope_version="0.1.0",
    check_engine_version=CHECK_ENGINE_VERSION,
    python_version="3.12.0",
    langgraph_version="1.2.0",
    data_hash="sha256:" + "d" * 64,
    provider="fake",
    model="scripted-careful",
    deterministic=True,
)
ALL_TABLES = [
    "runs",
    "spans",
    "datasets",
    "suite_runs",
    "case_results",
    "check_results",
    "experiments",
]


@pytest.fixture(params=["sqlite", pytest.param("postgresql", marks=pytest.mark.postgres)])
def repo(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[Repository]:
    if request.param == "sqlite":
        url = f"sqlite:///{tmp_path / 'repo.db'}"
    else:
        url = os.environ.get("AGENTSCOPE_TEST_POSTGRES_URL", "")
        if not url:
            pytest.skip("AGENTSCOPE_TEST_POSTGRES_URL not set")
    engine = create_db_engine(url)
    if request.param == "postgresql":
        with engine.begin() as connection:
            connection.execute(text("DROP SCHEMA public CASCADE"))
            connection.execute(text("CREATE SCHEMA public"))
    upgrade(engine)
    yield Repository(engine, Redactor(secret_values=[SECRET]))
    engine.dispose()


def config(version: str = "v2") -> AgentConfig:
    return AgentConfig(
        agent="research_assistant",
        version=version,
        prompt_version=version,
        tools=["search_documents"],
    )


def make_trace(
    task: str = "In what year did the bridge open?",
    *,
    status: RunStatus = RunStatus.COMPLETED,
    started_at: datetime = NOW,
    faults: Sequence[FaultSpec] = (),
    model_faults: Sequence[ModelFaultSpec] = (),
) -> TraceRecord:
    agent = config()
    trace_id = uuid.uuid4().hex
    ended_at = started_at + timedelta(milliseconds=5)
    return TraceRecord(
        trace_id=trace_id,
        run_id=str(uuid.uuid4()),
        agent=agent.agent,
        agent_version=agent.version,
        config_hash=agent.config_hash(),
        effective_config_hash=agent.config_hash(),
        config=agent,
        limits=agent.limits,
        permitted_tools=list(agent.tools),
        task=task,
        status=status,
        started_at=started_at,
        ended_at=ended_at,
        duration_ms=5.0,
        steps=1,
        provider="fake",
        model="scripted-careful",
        final_output={"answer": "1987"},
        faults=list(faults),
        model_faults=list(model_faults),
        spans=[
            SpanRecord(
                trace_id=trace_id,
                span_id=uuid.uuid4().hex[:16],
                name="invoke_agent research_assistant",
                kind=SpanKind.RUN,
                status=SpanStatus.OK,
                start_time=started_at,
                end_time=ended_at,
                duration_ms=5.0,
                input={"task": task},
                output={"answer": "1987"},
            )
        ],
    )


def make_case(
    case_id: str,
    trace: TraceRecord | None,
    *,
    passed: bool = True,
    message: str = "answer matched",
) -> CaseResult:
    if trace is None:
        return CaseResult(
            case_id=case_id,
            case_hash="sha256:" + "c" * 64,
            run_id=None,
            trace_id=None,
            passed=False,
            status=None,
            harness_error="provider crashed",
        )
    return CaseResult(
        case_id=case_id,
        case_hash="sha256:" + "c" * 64,
        run_id=trace.run_id,
        trace_id=trace.trace_id,
        passed=passed,
        status=trace.status,
        steps=1,
        checks=[
            CheckResult(
                name=CheckName.ANSWER_CORRECTNESS,
                status=CheckStatus.PASSED if passed else CheckStatus.FAILED,
                reason=ReasonCode.OK if passed else ReasonCode.MISMATCH,
                message=message,
            ),
            CheckResult(
                name=CheckName.TOOL_ORDER,
                status=CheckStatus.NOT_APPLICABLE,
                reason=ReasonCode.NO_EXPECTATION,
                message="no expected order",
            ),
        ],
    )


def make_dataset(version: str = "1", task: str = "In what year did the bridge open?") -> Dataset:
    return Dataset(
        name="geo",
        version=version,
        description="Geography facts",
        agent="research_assistant",
        cases=[
            EvalCase(
                id="bridge",
                task=task,
                expected=ExpectedAnswer(value="1987"),
                tags=["facts", "bridges"],
            ),
            EvalCase(id="ferry", task="How long is the ferry crossing?", tags=["facts"]),
        ],
    )


def make_header(*, version: str = "v2", label: str = "nightly") -> SuiteHeader:
    agent = config(version)
    return SuiteHeader(
        suite_id=str(uuid.uuid4()),
        label=label,
        dataset_name="geo",
        dataset_version="1",
        dataset_hash=make_dataset().content_hash(),
        agent_config=agent,
        config_hash=agent.config_hash(),
        effective_config_hash=agent.config_hash(),
        checks_enabled=sorted(CheckName),
        environment=ENVIRONMENT,
        started_at=NOW,
    )


def finished(header: SuiteHeader, cases: list[CaseResult]) -> SuiteResult:
    passed = sum(c.passed for c in cases)
    return SuiteResult.model_validate(
        {
            **header.model_dump(),
            "status": "completed",
            "finished_at": NOW + timedelta(seconds=3),
            "cases": cases,
            "metrics": SuiteMetrics(
                total=len(cases),
                passed=passed,
                failed=len(cases) - passed,
                harness_errors=0,
                pass_rate=passed / len(cases),
                checks={CheckName.ANSWER_CORRECTNESS: CheckStats(passed=passed)},
                injected_fault_outcomes=0,
                unexpected_tool_errors=0,
                invalid_outputs=0,
                repaired_outputs=0,
                policy_violation_cases=0,
                unauthorized_tool_attempts=0,
                unauthorized_tool_executions=0,
                avg_steps=1.0,
            ),
            "gate": GateResult(
                passed=True,
                rules=[GateRule(name="min_pass_rate", threshold="1/2", actual="1/1", passed=True)],
            ),
        }
    )


def run_suite(
    repo: Repository, *, version: str = "v2", passed: bool = True, label: str = "nightly"
) -> SuiteResult:
    header = make_header(version=version, label=label)
    repo.create_suite(header)
    trace = make_trace()
    repo.save_trace(trace, suite_id=header.suite_id)
    result = finished(header, [make_case("bridge", trace, passed=passed)])
    repo.record_case(header.suite_id, result.cases[0])
    repo.finish_suite(result)
    return result


def make_comparison(a: SuiteResult, b: SuiteResult) -> Comparison:
    def summary(suite: SuiteResult) -> SuiteSummary:
        return SuiteSummary(
            suite_id=suite.suite_id,
            agent_ref=suite.agent_config.ref,
            config_hash=suite.config_hash,
            effective_config_hash=suite.effective_config_hash,
            dataset_hash=suite.dataset_hash,
            metrics=suite.metrics,
        )

    return Comparison(
        experiment_id=str(uuid.uuid4()),
        name="v1 vs v2",
        created_at=NOW,
        dataset_name="geo",
        dataset_version="1",
        dataset_hash=a.dataset_hash,
        dataset_mismatch=False,
        deterministic=True,
        a=summary(a),
        b=summary(b),
        metric_deltas=[
            MetricDelta(
                metric="pass_rate", unit="pp", a=0, b=100, delta=100, higher_is_better=True, n=1
            )
        ],
        check_flips=[CheckFlip(check="answer_correctness", regressions=0, fixes=1)],
        cases=[
            CaseComparison(case_id="bridge", change=CaseChange.FIX, a_passed=False, b_passed=True)
        ],
        config_diff=[
            ConfigDifference(path="/prompt_version", dimension="prompt", a="v1", b="v2"),
            ConfigDifference(path="/version", dimension="other", a="v1", b="v2"),
        ],
        confounded=True,
    )


def counts(repo: Repository) -> dict[str, int]:
    return {table.name: table.rows for table in repo.inventory().tables}


# --- datasets ---------------------------------------------------------------------------


def test_dataset_round_trip_is_idempotent(repo: Repository) -> None:
    summary = repo.save_dataset(make_dataset())
    assert summary.content_hash == make_dataset().content_hash()
    assert (summary.case_count, summary.tags, summary.agent) == (
        2,
        ["bridges", "facts"],
        "research_assistant",
    )
    assert repo.save_dataset(make_dataset()) == summary

    newer = repo.save_dataset(make_dataset("2", task="When did the bridge open?"))
    detail = repo.get_dataset("geo", "1")
    assert detail is not None
    assert detail.summary == summary
    assert detail.dataset == make_dataset()
    latest = repo.latest_dataset("geo")
    assert latest is not None
    assert latest.summary == newer
    assert [d.version for d in repo.list_datasets()] == ["2", "1"]
    assert repo.get_dataset("geo", "9") is None
    assert repo.latest_dataset("missing") is None


def test_same_version_with_different_content_conflicts(repo: Repository) -> None:
    repo.save_dataset(make_dataset())
    with pytest.raises(DatasetConflict, match="geo@1"):
        repo.save_dataset(make_dataset(task="A different question?"))
    assert len(repo.list_datasets()) == 1


def test_dataset_is_redacted_before_hashing(repo: Repository) -> None:
    raw = make_dataset(task=f"Use the key {SECRET} to look it up")
    summary = repo.save_dataset(raw)
    detail = repo.get_dataset("geo", "1")
    assert detail is not None
    assert SECRET not in detail.dataset.model_dump_json()
    assert "[REDACTED:configured_secret]" in detail.dataset.cases[0].task
    # The stored hash describes the stored content, so it can always be re-verified.
    assert summary.content_hash == detail.dataset.content_hash() != raw.content_hash()
    assert repo.save_dataset(raw) == summary


# --- suites -----------------------------------------------------------------------------


def test_suite_lifecycle_round_trip(repo: Repository) -> None:
    header = make_header()
    repo.create_suite(header)
    trace = make_trace()
    repo.save_trace(trace, suite_id=header.suite_id)
    ok = make_case("bridge", trace)
    crashed = make_case("ferry", None)
    repo.record_case(header.suite_id, crashed)
    repo.record_case(header.suite_id, ok)

    items, total = repo.list_suites()
    assert total == 1
    item = items[0]
    assert (item.status, item.total, item.passed, item.pass_rate) == ("running", 2, 1, 0.5)
    assert (item.agent_ref, item.provider, item.deterministic) == (
        "research_assistant@v2",
        "fake",
        True,
    )
    assert item.gate_passed is None
    assert item.finished_at is None

    running = repo.get_suite(header.suite_id)
    assert running is not None
    assert running.status == "running"
    assert [c.case_id for c in running.cases] == ["bridge", "ferry"]  # sorted by case id
    progress = running.metrics
    assert (progress.total, progress.passed, progress.failed, progress.harness_errors) == (
        2,
        1,
        0,
        1,
    )
    assert progress.checks[CheckName.ANSWER_CORRECTNESS] == CheckStats(passed=1)
    assert progress.usage is None

    result = finished(header, [ok, crashed])
    repo.finish_suite(result)
    assert repo.get_suite(header.suite_id) == result
    item = repo.list_suites()[0][0]
    assert (item.status, item.gate_passed, item.finished_at) == (
        "completed",
        True,
        result.finished_at,
    )
    assert repo.list_runs(suite_id=header.suite_id)[1] == 1


def test_finish_suite_records_cases_it_has_not_seen(repo: Repository) -> None:
    header = make_header()
    repo.create_suite(header)
    result = finished(header, [make_case("ferry", None)])
    repo.finish_suite(result)
    assert repo.get_suite(header.suite_id) == result


def test_suite_errors(repo: Repository) -> None:
    missing = str(uuid.uuid4())
    with pytest.raises(LookupError):
        repo.record_case(missing, make_case("ferry", None))
    with pytest.raises(LookupError):
        repo.fail_suite(missing, "boom")
    header = make_header()
    repo.create_suite(header)
    with pytest.raises(ValueError, match="completed or errored"):
        repo.finish_suite(
            finished(header, [make_case("ferry", None)]).model_copy(update={"status": "running"})
        )
    assert repo.get_suite("not-a-uuid") is None
    assert repo.get_suite(missing) is None
    assert repo.list_runs(suite_id="not-a-uuid") == ([], 0)


def test_finished_suite_accepts_no_more_cases(repo: Repository) -> None:
    result = run_suite(repo)
    with pytest.raises(ValueError, match="already finished"):
        repo.record_case(result.suite_id, make_case("ferry", None))
    assert repo.get_suite(result.suite_id) == result
    item = repo.list_suites()[0][0]
    assert (item.total, item.passed) == (1, 1)


def test_record_case_counts_stored_rows(repo: Repository) -> None:
    """Totals come from the stored verdicts, so a stale in-memory count cannot win."""
    header = make_header()
    repo.create_suite(header)
    repo.record_case(header.suite_id, make_case("ferry", None))
    with repo.session() as session, session.begin():  # simulate a lost update
        row = session.get(SuiteRunRow, uuid.UUID(header.suite_id))
        assert row is not None
        row.total, row.passed = 0, 0
    trace = make_trace()
    repo.save_trace(trace, suite_id=header.suite_id)
    repo.record_case(header.suite_id, make_case("bridge", trace))
    item = repo.list_suites()[0][0]
    assert (item.total, item.passed, item.pass_rate) == (2, 1, 0.5)


def test_fail_suite_stores_a_redacted_error(repo: Repository) -> None:
    header = make_header()
    repo.create_suite(header)
    repo.fail_suite(header.suite_id, f"provider rejected {SECRET}")

    suite = repo.get_suite(header.suite_id)
    assert suite is not None
    assert suite.status == "error"
    assert suite.finished_at is not None
    assert suite.metrics.total == 0
    with repo.session() as session:
        row = session.get(SuiteRunRow, uuid.UUID(header.suite_id))
        assert row is not None
        assert row.error == "provider rejected [REDACTED:configured_secret]"
    assert repo.list_suites(status="error")[1] == 1


def test_list_suites_filters(repo: Repository) -> None:
    run_suite(repo, version="v1")
    run_suite(repo, version="v2")
    assert repo.list_suites(agent_ref="research_assistant@v1")[1] == 1
    assert repo.list_suites(dataset_name="geo")[1] == 2
    assert repo.list_suites(dataset_name="other")[1] == 0
    assert repo.list_suites(status="running")[1] == 0
    assert len(repo.list_suites(limit=1)[0]) == 1


def test_trace_case_and_verdict_are_redacted(repo: Repository) -> None:
    trace = make_trace(f"Look up {SECRET}")
    case = EvalCase(id="adhoc", task=f"Look up {SECRET}")
    repo.save_trace(trace, case=case, evaluation=make_case("adhoc", trace, message=SECRET))
    found = repo.get_run(trace.run_id)
    assert found is not None
    stored_trace, stored_case, stored_eval = found
    assert stored_case is not None
    assert stored_eval is not None
    for model in (stored_trace, stored_case, stored_eval):
        assert SECRET not in model.model_dump_json()


# --- failure analysis -------------------------------------------------------------------


def test_evaluated_cases_and_check_stats(repo: Repository) -> None:
    header = make_header()
    repo.create_suite(header)
    trace = make_trace(
        faults=[FaultSpec(tool="search_documents", kind=FaultKind.ERROR)],
        model_faults=[ModelFaultSpec(kind=ModelFaultKind.INVALID_JSON)],
    )
    repo.save_trace(trace, suite_id=header.suite_id)
    repo.record_case(header.suite_id, make_case("bridge", trace, passed=False))
    repo.record_case(header.suite_id, make_case("ferry", None))

    cases = repo.evaluated_cases([header.suite_id, "not-a-uuid"])
    assert cases == [
        EvaluatedCase(
            suite_id=header.suite_id,
            agent_ref="research_assistant@v2",
            deterministic=True,
            result=make_case("bridge", trace, passed=False),
            fault_kinds=("error", "invalid_json"),
        ),
        EvaluatedCase(
            suite_id=header.suite_id,
            agent_ref="research_assistant@v2",
            deterministic=True,
            result=make_case("ferry", None),
            fault_kinds=(),
        ),
    ]
    assert repo.check_stats([header.suite_id]) == {
        header.suite_id: {
            CheckName.ANSWER_CORRECTNESS: CheckStats(failed=1),
            CheckName.TOOL_ORDER: CheckStats(not_applicable=1),
        }
    }
    assert repo.evaluated_cases([]) == []


# --- experiments ------------------------------------------------------------------------


def test_experiment_round_trip(repo: Repository) -> None:
    a = run_suite(repo, version="v1", passed=False)
    b = run_suite(repo, version="v2")
    comparison = make_comparison(a, b)
    repo.save_experiment(comparison)

    assert repo.get_experiment(comparison.experiment_id) == comparison
    assert repo.get_experiment("not-a-uuid") is None
    items, total = repo.list_experiments()
    assert total == 1
    item = items[0]
    assert (item.a_ref, item.b_ref) == ("research_assistant@v1", "research_assistant@v2")
    assert (item.a_pass_rate, item.b_pass_rate, item.regressions, item.fixes) == (0, 1, 0, 1)
    assert (item.confounded, item.deterministic) == (True, True)


# --- crash recovery ---------------------------------------------------------------------


def test_mark_interrupted(repo: Repository) -> None:
    running = make_trace(status=RunStatus.RUNNING)
    done = make_trace()
    repo.save_trace(running)
    repo.save_trace(done)
    header = make_header()
    repo.create_suite(header)
    run_suite(repo)

    assert repo.mark_interrupted() == {"runs": 1, "suite_runs": 1}
    interrupted = repo.get_trace(running.run_id)
    assert interrupted is not None
    assert (interrupted.status, interrupted.error_code) == (RunStatus.ERROR, "interrupted")
    finished_trace = repo.get_trace(done.run_id)
    assert finished_trace is not None
    assert finished_trace.status is RunStatus.COMPLETED
    statuses = {s.suite_id: s.status for s in repo.list_suites()[0]}
    assert statuses[header.suite_id] == "error"
    assert sorted(statuses.values()) == ["completed", "error"]
    assert repo.mark_interrupted() == {"runs": 0, "suite_runs": 0}


# --- inventory and purge ----------------------------------------------------------------


def test_inventory(repo: Repository) -> None:
    repo.save_dataset(make_dataset())
    run_suite(repo)
    inventory = repo.inventory()
    assert [t.name for t in inventory.tables] == ALL_TABLES
    assert all(t.description for t in inventory.tables)
    assert counts(repo) == {
        "runs": 1,
        "spans": 1,
        "datasets": 1,
        "suite_runs": 1,
        "case_results": 1,
        "check_results": 2,
        "experiments": 0,
    }
    assert inventory.size_bytes is not None
    assert inventory.size_bytes > 0
    if inventory.backend == "sqlite":
        assert inventory.location.endswith("repo.db")
    else:
        assert "@" not in inventory.location  # never credentials


def _populate(repo: Repository) -> tuple[SuiteResult, SuiteResult, TraceRecord, Comparison]:
    repo.save_dataset(make_dataset())
    a = run_suite(repo, version="v1", passed=False)
    b = run_suite(repo, version="v2")
    comparison = make_comparison(a, b)
    repo.save_experiment(comparison)
    adhoc = make_trace()
    repo.save_trace(adhoc)
    return a, b, adhoc, comparison


def test_purge_evaluations_cascades(repo: Repository) -> None:
    a, _, adhoc, comparison = _populate(repo)
    result = repo.purge("evaluations")
    assert result.deleted == {
        "runs": 2,
        "spans": 2,
        "datasets": 0,
        "suite_runs": 2,
        "case_results": 2,
        "check_results": 4,
        "experiments": 1,
    }
    assert repo.get_suite(a.suite_id) is None
    assert repo.get_experiment(comparison.experiment_id) is None
    assert repo.get_trace(adhoc.run_id) is not None
    assert result.size_bytes_before is not None
    assert result.size_bytes_after is not None


def test_purge_runs_keeps_verdicts(repo: Repository) -> None:
    a, _, _, comparison = _populate(repo)
    result = repo.purge("runs")
    assert result.deleted["runs"] == 3
    assert result.deleted["spans"] == 3
    assert result.deleted["case_results"] == 0
    suite = repo.get_suite(a.suite_id)
    assert suite is not None
    assert (suite.cases[0].run_id, suite.cases[0].trace_id) == (None, None)
    assert repo.get_experiment(comparison.experiment_id) == comparison


@pytest.mark.parametrize(
    ("scope", "table"), [("experiments", "experiments"), ("datasets", "datasets")]
)
def test_purge_single_tables(repo: Repository, scope: str, table: str) -> None:
    _populate(repo)
    before = counts(repo)
    result = repo.purge(scope)
    assert result.deleted == {name: before[name] if name == table else 0 for name in ALL_TABLES}


def test_purge_all_and_before(repo: Repository) -> None:
    old = make_trace(started_at=NOW - timedelta(days=30))
    recent = make_trace(started_at=NOW)
    repo.save_trace(old)
    repo.save_trace(recent)
    assert repo.purge("runs", before=NOW - timedelta(days=1)).deleted["runs"] == 1
    assert repo.get_trace(old.run_id) is None
    assert repo.get_trace(recent.run_id) is not None
    with pytest.raises(ValueError, match="timezone-aware"):
        repo.purge(before=datetime(2026, 1, 1))

    _populate(repo)
    repo.purge("all")
    assert set(counts(repo).values()) == {0}


def test_purge_leaves_no_trace_in_sqlite_files(tmp_path: Path) -> None:
    path = tmp_path / "private.db"
    engine = create_db_engine(f"sqlite:///{path}")
    upgrade(engine)
    repo = Repository(engine)
    planted = f"PLANTED-{uuid.uuid4().hex}"

    def files_contain_planted() -> bool:
        files = (path, path.with_name(path.name + "-wal"))
        return any(planted.encode() in f.read_bytes() for f in files if f.exists())

    repo.save_dataset(make_dataset(task=f"{planted} in a dataset"))
    a = run_suite(repo, label=planted)
    with engine.connect() as connection:  # move what exists so far into the main file
        connection.exec_driver_sql("PRAGMA wal_checkpoint(TRUNCATE)")
    repo.save_trace(make_trace(task=f"{planted} in a run"))
    b = run_suite(repo)
    comparison = make_comparison(a, b).model_copy(update={"name": planted})
    repo.save_experiment(comparison)
    assert files_contain_planted()

    result = repo.purge("all")

    assert not files_contain_planted()
    assert result.size_bytes_before is not None
    assert result.size_bytes_after is not None
    assert result.size_bytes_after < result.size_bytes_before
    engine.dispose()


def test_purge_reports_content_a_reader_kept_on_disk(tmp_path: Path) -> None:
    """A reader blocks the WAL truncation; purge must say so instead of claiming success."""
    path = tmp_path / "private.db"
    engine = create_db_engine(f"sqlite:///{path}")
    # Fail fast instead of waiting the default five seconds for the reader.
    event.listen(engine, "connect", lambda dbapi, _: dbapi.execute("PRAGMA busy_timeout=100"))
    upgrade(engine)
    repo = Repository(engine)
    planted = f"PLANTED-{uuid.uuid4().hex}"

    def files_contain_planted() -> bool:
        files = (path, path.with_name(path.name + "-wal"))
        return any(planted.encode() in f.read_bytes() for f in files if f.exists())

    repo.save_trace(make_trace(task=f"{planted} in a run"))
    reader = sqlite3.connect(path)
    try:
        reader.execute("BEGIN")
        assert reader.execute("SELECT count(*) FROM runs").fetchone() == (1,)
        with pytest.raises(PurgeIncomplete, match="purge again") as raised:
            repo.purge("runs")
        assert raised.value.result.deleted["runs"] == 1
        assert files_contain_planted()
    finally:
        reader.close()

    assert repo.purge("runs").deleted["runs"] == 0  # nothing left to delete, but it compacts
    assert not files_contain_planted()
    engine.dispose()
