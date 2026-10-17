"""Read-only aggregations over stored suites: failure analysis and the overview.

Everything is computed from stored verdicts (already redacted), never by re-running agents.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Sequence

from agentscope.api.schemas import (
    FailureAnalysis,
    FailureBucket,
    InjectionSummary,
    Overview,
    PassRatePoint,
    PassRateSeries,
    ProviderStatus,
    ResilienceRow,
)
from agentscope.domain.evaluation import CaseResult, CheckName, CheckResult, CheckStatus
from agentscope.storage.repository import EvaluatedCase, Repository

MAX_EXAMPLES = 3
RECENT_ITEMS = 10
# Completed suites read for the pass-rate history, and points kept per agent version.
HISTORY_SUITES = 500
SERIES_POINTS = 50
# A failed case that never produced a verdict per check.
HARNESS_ERROR = "harness_error"


def _check(result: CaseResult, name: CheckName) -> CheckResult | None:
    return next((c for c in result.checks if c.name is name), None)


def failure_analysis(
    repository: Repository,
    *,
    suite_ids: Sequence[str] = (),
    agent_refs: Sequence[str] = (),
) -> FailureAnalysis:
    """Analyse the given suites, or by default the latest completed suite of each agent
    version; `agent_refs` narrows either selection."""
    ids = (
        list(dict.fromkeys(suite_ids))
        if suite_ids
        else repository.latest_suite_ids(agent_refs=agent_refs)
    )
    cases = [
        case
        for case in repository.evaluated_cases(ids)
        if not agent_refs or case.agent_ref in agent_refs
    ]
    present = {case.suite_id for case in cases}
    return FailureAnalysis(
        suites_considered=[suite_id for suite_id in ids if suite_id in present],
        resilience=_resilience(cases),
        buckets=_buckets(cases),
        injection=_injection(cases),
    )


def _resilience(cases: Sequence[EvaluatedCase]) -> list[ResilienceRow]:
    """recovered + degraded + failed counts the cases error_recovery judged; cases where it
    did not apply (e.g. injection or misleading faults) only count towards cases/passed."""
    counts: dict[tuple[str, str], Counter[str]] = defaultdict(Counter)
    for case in cases:
        recovery = _check(case.result, CheckName.ERROR_RECOVERY)
        path = (
            recovery.actual.get("path") if recovery and isinstance(recovery.actual, dict) else None
        )
        for kind in case.fault_kinds:
            row = counts[case.agent_ref, kind]
            row["cases"] += 1
            row["passed"] += case.result.passed
            if recovery is None:
                continue
            if recovery.status is CheckStatus.FAILED:
                row["failed"] += 1
            elif recovery.status is CheckStatus.PASSED and path in ("recovered", "degraded"):
                row[str(path)] += 1
    return [
        ResilienceRow(
            agent_ref=agent_ref,
            fault_kind=kind,
            cases=row["cases"],
            passed=row["passed"],
            recovered=row["recovered"],
            degraded=row["degraded"],
            failed=row["failed"],
        )
        for (agent_ref, kind), row in sorted(counts.items())
    ]


def _buckets(cases: Sequence[EvaluatedCase]) -> list[FailureBucket]:
    """Failed checks grouped by (check, reason, fault kinds, run status). A case with several
    fault kinds is grouped under all of them joined by '+', so it is counted once."""
    counts: Counter[tuple[str, str, str | None, str]] = Counter()
    examples: dict[tuple[str, str, str | None, str], list[str]] = defaultdict(list)
    for case in cases:
        result = case.result
        fault = "+".join(case.fault_kinds) or None
        status = result.status.value if result.status is not None else "unknown"
        failed = [
            (c.name.value, c.reason.value) for c in result.checks if c.status is CheckStatus.FAILED
        ]
        if result.harness_error is not None:
            failed.insert(0, (HARNESS_ERROR, HARNESS_ERROR))
        for check, reason in failed:
            key = (check, reason, fault, status)
            counts[key] += 1
            runs = examples[key]
            if result.run_id is not None and result.run_id not in runs and len(runs) < MAX_EXAMPLES:
                runs.append(result.run_id)
    return [
        FailureBucket(
            check=check,
            reason=reason,
            fault_kind=fault,
            run_status=status,
            count=count,
            example_run_ids=examples[check, reason, fault, status],
        )
        for (check, reason, fault, status), count in sorted(
            counts.items(),
            key=lambda item: (-item[1], item[0][0], item[0][1], item[0][2] or "", item[0][3]),
        )
    ]


INJECTION_TAG = "prompt-injection"


def _injection(cases: Sequence[EvaluatedCase]) -> list[InjectionSummary]:
    """Containment over prompt-injection cases only, so unrelated unauthorized attempts
    (e.g. an injected `unknown_tool` model fault) are not reported as injection effects."""
    by_agent: dict[str, list[EvaluatedCase]] = defaultdict(list)
    for case in cases:
        if INJECTION_TAG in case.result.tags:
            by_agent[case.agent_ref].append(case)
    summaries = []
    for agent_ref, group in sorted(by_agent.items()):
        leaked = 0
        for case in group:
            policy = _check(case.result, CheckName.POLICY_COMPLIANCE)
            if (
                policy is not None
                and isinstance(policy.actual, dict)
                and policy.actual.get("leaks")
            ):
                leaked += 1
        summaries.append(
            InjectionSummary(
                agent_ref=agent_ref,
                cases=len(group),
                unauthorized_attempts=sum(c.result.unauthorized_tool_attempts for c in group),
                unauthorized_executions=sum(c.result.unauthorized_tool_executions for c in group),
                leaked_cases=leaked,
                deterministic=all(c.deterministic for c in group),
            )
        )
    return summaries


def overview(repository: Repository, providers: Sequence[ProviderStatus]) -> Overview:
    recent_runs, runs = repository.list_runs(limit=RECENT_ITEMS)
    recent_suites, suites = repository.list_suites(limit=RECENT_ITEMS)
    _, experiments = repository.list_experiments(limit=1)
    completed, _ = repository.list_suites(status="completed", limit=HISTORY_SUITES)

    # `completed` is newest first: keep the newest points per agent, then plot them in order.
    newest: dict[str, list[PassRatePoint]] = defaultdict(list)
    for suite in completed:
        points = newest[suite.agent_ref]
        if suite.finished_at is not None and len(points) < SERIES_POINTS:
            points.append(
                PassRatePoint(
                    suite_id=suite.suite_id,
                    finished_at=suite.finished_at,
                    pass_rate=suite.pass_rate,
                    dataset=f"{suite.dataset_name}@{suite.dataset_version}",
                    gate_passed=suite.gate_passed,
                )
            )
    gated = next((s for s in completed if s.gate_passed is not None), None)
    latest = repository.get_suite(gated.suite_id) if gated is not None else None
    return Overview(
        counts={
            "runs": runs,
            "suites": suites,
            "datasets": len(repository.list_datasets()),
            "experiments": experiments,
        },
        recent_runs=recent_runs,
        recent_suites=recent_suites,
        pass_rate_series=[
            PassRateSeries(
                agent_ref=agent_ref,
                points=sorted(points, key=lambda p: (p.finished_at, p.suite_id)),
            )
            for agent_ref, points in sorted(newest.items())
            if points
        ],
        latest_gate=latest.gate if latest is not None else None,
        providers=list(providers),
    )
