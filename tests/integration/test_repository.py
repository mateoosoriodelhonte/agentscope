"""Repository round trips, conflicts, crash recovery, inventory and purge, on SQLite and (when
configured) PostgreSQL."""

from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
import uuid
from collections.abc import Callable, Iterator, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import event, text

from agentscope.domain.ablations import Ablation
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
    Evidence,
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
from agentscope.experiments.ablation import ablatable_dimensions, attribute, plan_variants
from agentscope.storage.db import create_db_engine, upgrade
from agentscope.storage.liveness import owner_alive, process_owner, this_host
from agentscope.storage.models import SuiteRunRow
from agentscope.storage.repository import (
    TRUNCATED,
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
    "ablations",
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


def make_header(
    *, version: str = "v2", label: str = "nightly", agent: AgentConfig | None = None
) -> SuiteHeader:
    agent = agent or config(version)
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
    repo: Repository,
    *,
    version: str = "v2",
    passed: bool = True,
    label: str = "nightly",
    agent: AgentConfig | None = None,
    ablation_id: str | None = None,
) -> SuiteResult:
    header = make_header(version=version, label=label, agent=agent)
    repo.create_suite(header, ablation_id=ablation_id)
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


def test_list_pages_and_totals_come_from_one_statement(repo: Repository) -> None:
    """A row committed between a separate count and page query made them disagree."""
    for _ in range(3):
        run_suite(repo)
    statements: list[str] = []

    def record(_conn: object, _cursor: object, statement: str, *_args: object) -> None:
        if statement.lstrip().upper().startswith("SELECT"):
            statements.append(statement)

    event.listen(repo.engine, "before_cursor_execute", record)
    try:
        for listing in (repo.list_runs, repo.list_suites, repo.list_experiments):
            statements.clear()
            listing(limit=2)
            assert len(statements) <= 1, listing
    finally:
        event.remove(repo.engine, "before_cursor_execute", record)
    runs, total = repo.list_runs(limit=2)
    assert (len(runs), total) == (2, 3)
    assert repo.list_runs(limit=2, offset=2)[1] == 3
    assert repo.list_runs(limit=2, offset=10) == ([], 3)
    assert repo.list_suites(limit=1, offset=1)[1] == 3
    assert repo.list_runs(agent="nobody") == ([], 0)


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


def test_redaction_markers_never_push_a_field_past_its_limit(repo: Repository) -> None:
    """A message cut to its limit before redaction can grow when a secret is replaced by a
    longer marker; the stored copy is cut again instead of failing the save."""
    key = "AKIAABCDEFGHIJKLMNOP"  # 20 characters, replaced by a 25-character marker
    trace = make_trace()
    case = make_case("bridge", trace, message="x" * 979 + " " + key)
    long_detail = CheckResult(
        name=CheckName.POLICY_COMPLIANCE,
        status=CheckStatus.FAILED,
        reason=ReasonCode.LEAKED_CONTENT,
        message="leaked",
        evidence=[Evidence(role="violation", detail="y" * 486 + " password=abcd")],
    )
    case = case.model_copy(update={"checks": [*case.checks, long_detail]})
    header = make_header()
    repo.create_suite(header)
    repo.save_trace(trace, suite_id=header.suite_id, evaluation=case)
    repo.record_case(header.suite_id, case)
    repo.finish_suite(finished(header, [case]))

    stored = repo.get_suite(header.suite_id)
    assert stored is not None
    message = stored.cases[0].checks[0].message
    assert len(message) == 1000
    assert key not in message
    assert message.endswith(TRUNCATED)
    detail = stored.cases[0].checks[2].evidence[0].detail
    assert len(detail) == 500
    assert "abcd" not in detail
    found = repo.get_run(trace.run_id)
    assert found is not None
    assert found[2] is not None
    assert key not in found[2].model_dump_json()


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


def test_find_experiment_by_its_two_suites(repo: Repository) -> None:
    a = run_suite(repo, version="v1", passed=False)
    b = run_suite(repo, version="v2")
    assert repo.find_experiment(a.suite_id, b.suite_id) is None
    comparison = make_comparison(a, b)
    repo.save_experiment(comparison)
    assert repo.find_experiment(a.suite_id, b.suite_id) == comparison
    assert repo.find_experiment(b.suite_id, a.suite_id) is None  # A is the reference
    assert repo.find_experiment("not-a-uuid", b.suite_id) is None


# --- ablations --------------------------------------------------------------------------


def save_ablation(
    repo: Repository,
    a: SuiteResult,
    b: SuiteResult,
    experiment_id: str | None = None,
    created_at: datetime = NOW,
) -> tuple[Ablation, list[str]]:
    """An ablation of A and B whose variant suites are stored linked to it, as services
    store them; returns it with the variant suite ids."""
    ablation_id = str(uuid.uuid4())
    fields = ablatable_dimensions(a.agent_config, b.agent_config)
    evaluated = [
        (variant, run_suite(repo, agent=variant.config, ablation_id=ablation_id))
        for variant in plan_variants(a.agent_config, b.agent_config, fields)
    ]
    ablation = attribute(
        a,
        b,
        evaluated,
        name=f"why {SECRET}",
        ablation_id=ablation_id,
        experiment_id=experiment_id,
        created_at=created_at,
    )
    repo.save_ablation(ablation)
    return ablation, [suite.suite_id for _, suite in evaluated]


def test_ablation_round_trip_is_redacted(repo: Repository) -> None:
    a = run_suite(repo, version="v1", passed=False)
    b = run_suite(repo, version="v2")
    comparison = make_comparison(a, b)
    repo.save_experiment(comparison)
    ablation, variants = save_ablation(repo, a, b, comparison.experiment_id)
    assert ablation.dimensions == ["prompt"]
    assert len(variants) == 2

    stored = repo.get_ablation(ablation.ablation_id)
    assert stored is not None
    assert SECRET not in stored.model_dump_json()
    assert stored == ablation.model_copy(update={"name": stored.name})
    assert repo.get_ablation("not-a-uuid") is None
    assert repo.get_ablation(str(uuid.uuid4())) is None
    assert repo.latest_ablation_for_experiment(comparison.experiment_id) == stored
    assert repo.latest_ablation_for_experiment(str(uuid.uuid4())) is None

    items, total = repo.list_ablations()
    assert total == 1
    item = items[0]
    assert (item.ablation_id, item.experiment_id, item.a_ref, item.b_ref) == (
        ablation.ablation_id,
        comparison.experiment_id,
        "research_assistant@v1",
        "research_assistant@v2",
    )
    assert (item.dimensions, item.deterministic) == (["prompt"], True)
    assert (item.total_effect_pp, item.interaction_pp) == (100.0, 0.0)
    assert repo.list_ablations(experiment_id=comparison.experiment_id)[1] == 1
    assert repo.list_ablations(experiment_id=str(uuid.uuid4())) == ([], 0)
    assert repo.list_ablations(experiment_id="not-a-uuid") == ([], 0)
    assert repo.list_ablations(limit=1, offset=1) == ([], 1)


def test_variant_suites_are_hidden_unless_asked_for(repo: Repository) -> None:
    a = run_suite(repo, version="v1", passed=False)
    b = run_suite(repo, version="v2")
    _, variants = save_ablation(repo, a, b)
    assert {s.suite_id for s in repo.list_suites()[0]} == {a.suite_id, b.suite_id}
    listed, total = repo.list_suites(include_ablations=True)
    assert total == 4
    assert {s.suite_id for s in listed} >= set(variants)
    # Variants are not agent versions anyone chose: failure analysis never picks them.
    assert set(repo.latest_suite_ids()) == {a.suite_id, b.suite_id}
    for suite_id in variants:
        assert repo.get_suite(suite_id) is not None  # still a normal, readable suite


def test_discarding_variants_spares_a_stored_ablation(repo: Repository) -> None:
    """What an unfinished ablation stored goes (with its runs); a stored one keeps its own."""
    a = run_suite(repo, version="v1", passed=False)
    b = run_suite(repo, version="v2")
    ablation, variants = save_ablation(repo, a, b)
    assert repo.discard_ablation_variants(ablation.ablation_id) == 0
    assert all(repo.get_suite(suite_id) is not None for suite_id in variants)

    unfinished = str(uuid.uuid4())
    stray = run_suite(repo, version="v3", ablation_id=unfinished)
    runs = repo.list_runs(include_ablations=True)[1]
    assert repo.discard_ablation_variants(unfinished) == 1
    assert repo.get_suite(stray.suite_id) is None
    assert repo.list_runs(include_ablations=True)[1] == runs - 1
    assert repo.discard_ablation_variants("not-a-uuid") == 0


def test_an_experiment_deleted_alone_unlinks_its_ablation(repo: Repository) -> None:
    a = run_suite(repo, version="v1", passed=False)
    b = run_suite(repo, version="v2")
    comparison = make_comparison(a, b)
    repo.save_experiment(comparison)
    ablation, _ = save_ablation(repo, a, b, comparison.experiment_id)
    with repo.session() as session, session.begin():
        session.execute(text("DELETE FROM experiments"))
    stored = repo.get_ablation(ablation.ablation_id)
    assert stored is not None
    assert stored.experiment_id is None
    assert repo.list_ablations()[0][0].experiment_id is None


# --- crash recovery ---------------------------------------------------------------------


def _set_owner(
    repo: Repository, suite_id: str, owner: str | None, heartbeat_at: datetime | None
) -> None:
    """Pretend another process owns a running suite."""
    with repo.session() as session, session.begin():
        row = session.get(SuiteRunRow, uuid.UUID(suite_id))
        assert row is not None
        row.owner, row.heartbeat_at = owner, heartbeat_at


def _alive(owners: dict[str, bool | None]) -> Callable[[str | None], bool | None]:
    return lambda owner: owners.get(owner or "")


def test_mark_interrupted_spares_live_work(repo: Repository) -> None:
    """A suite another process is still running is never marked: a live process on this
    host however long its heartbeat was silent (it may be suspended), a process elsewhere
    while its heartbeat is fresh."""
    mine = make_header()
    repo.create_suite(mine)  # owned by this process, which is obviously alive
    _set_owner(repo, mine.suite_id, process_owner(), datetime.now(UTC) - timedelta(days=1))
    remote = make_header()
    repo.create_suite(remote)
    _set_owner(repo, remote.suite_id, "another-host.invalid:41", datetime.now(UTC))

    assert repo.mark_interrupted() == {"runs": 0, "suite_runs": 0}
    assert {s.status for s in repo.list_suites()[0]} == {"running"}
    trace = make_trace()
    repo.save_trace(trace, suite_id=mine.suite_id)
    repo.record_case(mine.suite_id, make_case("bridge", trace))  # still accepted


def test_mark_interrupted_marks_dead_or_silent_owners(repo: Repository) -> None:
    now = datetime.now(UTC)
    dead = make_header()
    repo.create_suite(dead)
    _set_owner(repo, dead.suite_id, "this-host:43", now)
    silent = make_header()
    repo.create_suite(silent)
    _set_owner(repo, silent.suite_id, "other-host:44", now - timedelta(minutes=10))
    legacy = make_header()  # written before owners were recorded; started long ago (NOW)
    repo.create_suite(legacy)
    _set_owner(repo, legacy.suite_id, None, None)
    done = run_suite(repo)
    running = make_trace(status=RunStatus.RUNNING)
    repo.save_trace(running)
    with repo.session() as session, session.begin():
        session.execute(text("UPDATE runs SET owner = 'this-host:43' WHERE status = 'running'"))

    alive = _alive({"this-host:43": False, "other-host:44": None})
    assert repo.mark_interrupted(alive=alive) == {"runs": 1, "suite_runs": 3}

    for header in (dead, silent, legacy):
        suite = repo.get_suite(header.suite_id)
        assert suite is not None
        assert suite.status == "error"
        assert suite.error is not None
        assert suite.error.startswith("interrupted")
        # A finish time, so a late writer is refused instead of reviving the suite.
        assert suite.finished_at is not None
        with pytest.raises(ValueError, match="already finished"):
            repo.record_case(header.suite_id, make_case("ferry", None))
        with pytest.raises(ValueError, match="already finished"):
            repo.finish_suite(finished(header, [make_case("ferry", None)]))
        assert repo.get_suite(header.suite_id) == suite
    interrupted = repo.get_trace(running.run_id)
    assert interrupted is not None
    assert (interrupted.status, interrupted.error_code) == (RunStatus.ERROR, "interrupted")
    assert repo.get_suite(done.suite_id) == done
    assert repo.mark_interrupted(alive=alive) == {"runs": 0, "suite_runs": 0}


def test_running_suites_record_their_owner_and_heartbeat(repo: Repository) -> None:
    header = make_header()
    repo.create_suite(header)

    def heartbeat() -> datetime:
        with repo.session() as session:
            row = session.get(SuiteRunRow, uuid.UUID(header.suite_id))
            assert row is not None
            assert row.owner == process_owner()
            assert row.heartbeat_at is not None
            return row.heartbeat_at

    first = heartbeat()
    repo.heartbeat(header.suite_id)
    second = heartbeat()
    repo.record_case(header.suite_id, make_case("ferry", None))
    assert first <= second <= heartbeat()


def test_owner_alive_probes_processes_on_this_host() -> None:
    child = subprocess.Popen([sys.executable, "-c", ""])
    child.wait()  # reaped: its pid names no process any more
    host = this_host()
    assert owner_alive(process_owner()) is True
    assert owner_alive(f"{host}:{child.pid}") is False
    assert owner_alive(f"{host}:{child.pid}:a1b2c3d4e5f6") is False
    assert owner_alive(f"{host}:{os.getppid()}:a1b2c3d4e5f6") is True  # the parent lives
    assert owner_alive(f"{host}:0") is None
    assert owner_alive("another-host.example:1") is None
    assert owner_alive("another-host.example:1:a1b2c3d4e5f6") is None
    assert owner_alive(None) is None
    assert owner_alive("garbage") is None


def test_an_earlier_process_with_this_pid_is_gone() -> None:
    """A server restarted in its container is pid 1 again: the suites its predecessor left
    running carry the same host and pid, but not this process's incarnation."""
    mine = process_owner()
    host, pid, incarnation = mine.rsplit(":", 2)
    assert (host, pid) == (this_host(), str(os.getpid()))
    predecessor = f"{host}:{pid}:{'0' * len(incarnation)}"
    assert predecessor != mine
    assert owner_alive(predecessor) is False
    assert owner_alive(f"{host}:{pid}") is True  # no incarnation recorded: cannot tell


def test_recovery_marks_suites_an_earlier_process_with_this_pid_left(repo: Repository) -> None:
    header = make_header()
    repo.create_suite(header)
    host, pid, _ = process_owner().rsplit(":", 2)
    _set_owner(repo, header.suite_id, f"{host}:{pid}:000000000000", datetime.now(UTC))
    assert repo.mark_interrupted() == {"runs": 0, "suite_runs": 1}
    suite = repo.get_suite(header.suite_id)
    assert suite is not None
    assert suite.status == "error"


@pytest.mark.filterwarnings("ignore:This process .* is multi-threaded:DeprecationWarning")
def test_a_forked_child_is_a_process_of_its_own() -> None:
    if not hasattr(os, "fork"):
        pytest.skip("no fork on this platform")
    read, write = os.pipe()
    pid = os.fork()
    if pid == 0:  # pragma: no cover - runs in the child
        os.close(read)
        os.write(write, process_owner().encode())
        os._exit(0)
    os.close(write)
    with os.fdopen(read, "rb") as pipe:
        child = pipe.read().decode()
    os.waitpid(pid, 0)
    assert child.rsplit(":", 2)[2] != process_owner().rsplit(":", 2)[2]


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
        "ablations": 0,
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
        "ablations": 0,
    }
    assert repo.get_suite(a.suite_id) is None
    assert repo.get_experiment(comparison.experiment_id) is None
    assert repo.get_trace(adhoc.run_id) is not None
    assert result.size_bytes_before is not None
    assert result.size_bytes_after is not None


def test_purging_evaluations_takes_ablations_and_their_variants(repo: Repository) -> None:
    a, b, adhoc, comparison = _populate(repo)
    ablation, variants = save_ablation(repo, a, b, comparison.experiment_id)
    result = repo.purge("evaluations")
    # A and B, their experiment and ablation (cascaded), and the two variant suites.
    assert (result.deleted["suite_runs"], result.deleted["runs"]) == (4, 4)
    assert (result.deleted["experiments"], result.deleted["ablations"]) == (1, 1)
    assert repo.get_ablation(ablation.ablation_id) is None
    assert all(repo.get_suite(suite_id) is None for suite_id in variants)
    assert repo.get_trace(adhoc.run_id) is not None


def test_purging_experiments_takes_ablations_and_their_variants(repo: Repository) -> None:
    a, b, _, comparison = _populate(repo)
    ablation, variants = save_ablation(repo, a, b, comparison.experiment_id)
    later = run_suite(repo, version="v3")
    _, kept = save_ablation(repo, b, later, created_at=NOW + timedelta(days=1))
    result = repo.purge("experiments", before=NOW + timedelta(hours=1))
    assert (result.deleted["experiments"], result.deleted["ablations"]) == (1, 1)
    assert result.deleted["suite_runs"] == len(variants)
    assert repo.get_ablation(ablation.ablation_id) is None
    assert all(repo.get_suite(suite_id) is None for suite_id in variants)
    # A, B and the newer ablation with its variants stay.
    assert all(repo.get_suite(s) is not None for s in (a.suite_id, b.suite_id, *kept))
    assert repo.list_ablations()[1] == 1
    assert repo.purge("experiments").deleted["suite_runs"] == len(kept)


def test_a_variant_suite_that_is_another_ablations_side_goes_with_it(repo: Repository) -> None:
    """Deleting an ablation's variant suites cascades to an ablation built on one of them
    (here one too new for `before`), whose own variants must then go as well."""
    a = run_suite(repo, version="v1", passed=False)
    b = run_suite(repo, version="v2")
    c = run_suite(repo, version="v3")
    _, variants = save_ablation(repo, a, b)
    variant = repo.get_suite(variants[0])
    assert variant is not None
    second, nested = save_ablation(repo, variant, c, created_at=NOW + timedelta(days=1))
    assert nested
    result = repo.purge("experiments", before=NOW + timedelta(hours=1))
    assert result.deleted["ablations"] == 2
    assert result.deleted["suite_runs"] == len(variants) + len(nested)
    assert repo.get_ablation(second.ablation_id) is None
    remaining = repo.list_suites(include_ablations=True)[0]
    assert {s.suite_id for s in remaining} == {a.suite_id, b.suite_id, c.suite_id}


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


def test_purge_keeps_the_datasets_it_is_told_to(repo: Repository) -> None:
    packaged = repo.save_dataset(make_dataset())
    repo.save_dataset(make_dataset("2", task="When did the bridge open?"))
    for scope in ("datasets", "all"):
        result = repo.purge(scope, keep_datasets={packaged.content_hash})
        assert [(d.name, d.version) for d in repo.list_datasets()] == [("geo", "1")]
    assert result.deleted["datasets"] == 0
    assert repo.purge("datasets").deleted["datasets"] == 1


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
