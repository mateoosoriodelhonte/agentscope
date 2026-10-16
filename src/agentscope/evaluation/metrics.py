"""Aggregate suite metrics from case results.

Everything except `timings` is a pure function of the case results and the
provider-reported usage, so the same runs always give the same metrics. Rates are plain
`passed / total`: a harness error counts as not passed, never as excluded.
"""

from __future__ import annotations

import math
import statistics
from collections import Counter
from collections.abc import Mapping, Sequence

from agentscope.domain.evaluation import (
    CaseResult,
    CheckName,
    CheckStats,
    CheckStatus,
    InformationalTimings,
    SuiteMetrics,
    UsageSummary,
)
from agentscope.domain.traces import TokenUsage


def compute_metrics(
    cases: Sequence[CaseResult], usage: Mapping[str, TokenUsage | None] | None = None
) -> SuiteMetrics:
    """`usage` maps run ids to what the provider reported for that run (None = nothing)."""
    total = len(cases)
    passed = sum(1 for c in cases if c.passed)
    harness_errors = sum(1 for c in cases if c.harness_error is not None)
    ran = [c for c in cases if c.run_id is not None]
    statuses = Counter(c.status.value for c in cases if c.status is not None)
    return SuiteMetrics(
        total=total,
        passed=passed,
        failed=total - passed - harness_errors,
        harness_errors=harness_errors,
        pass_rate=passed / total if total else 0.0,
        run_statuses=dict(sorted(statuses.items())),
        checks=check_stats(cases),
        injected_fault_outcomes=sum(c.injected_fault_outcomes for c in cases),
        unexpected_tool_errors=sum(c.unexpected_tool_errors for c in cases),
        invalid_outputs=sum(1 for c in cases if c.invalid_output),
        repaired_outputs=sum(1 for c in cases if c.repaired_output),
        policy_violation_cases=sum(1 for c in cases if _failed(c, CheckName.POLICY_COMPLIANCE)),
        unauthorized_tool_attempts=sum(c.unauthorized_tool_attempts for c in cases),
        unauthorized_tool_executions=sum(c.unauthorized_tool_executions for c in cases),
        avg_steps=statistics.fmean(c.steps for c in ran) if ran else 0.0,
        usage=usage_summary(ran, usage or {}),
        timings=_timings([c.duration_ms for c in ran]),
    )


def check_stats(cases: Sequence[CaseResult]) -> dict[CheckName, CheckStats]:
    """Per-check counts over the cases that produced check results, in check order."""
    counts: dict[CheckName, Counter[CheckStatus]] = {}
    for case in cases:
        for check in case.checks:
            counts.setdefault(check.name, Counter())[check.status] += 1
    return {
        name: CheckStats(
            passed=counts[name][CheckStatus.PASSED],
            failed=counts[name][CheckStatus.FAILED],
            not_applicable=counts[name][CheckStatus.NOT_APPLICABLE],
        )
        for name in CheckName
        if name in counts
    }


def usage_summary(
    ran: Sequence[CaseResult], usage: Mapping[str, TokenUsage | None]
) -> UsageSummary | None:
    """Totals over the runs whose provider reported usage for every model call. A run with
    partial usage is treated as not reporting, so the totals cover exactly
    `reporting_runs` runs; the summary is partial when that is fewer than `total_runs`."""
    complete = [
        u
        for c in ran
        if c.run_id is not None
        and (u := usage.get(c.run_id)) is not None
        and u.complete
        and u.input_tokens is not None
        and u.output_tokens is not None
    ]
    if not complete:
        return None
    return UsageSummary(
        input_tokens=sum(u.input_tokens or 0 for u in complete),
        output_tokens=sum(u.output_tokens or 0 for u in complete),
        reporting_runs=len(complete),
        total_runs=len(ran),
    )


def _failed(case: CaseResult, name: CheckName) -> bool:
    return any(c.name is name and c.status is CheckStatus.FAILED for c in case.checks)


def _timings(durations: list[float]) -> InformationalTimings | None:
    if not durations:
        return None
    ordered = sorted(durations)
    p95 = ordered[math.ceil(0.95 * len(ordered)) - 1]  # nearest-rank
    return InformationalTimings(median_duration_ms=statistics.median(ordered), p95_duration_ms=p95)
