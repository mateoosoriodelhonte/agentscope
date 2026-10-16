"""Suite metrics: honest denominators, per-check counts, and usage only when reported."""

from __future__ import annotations

from typing import Any

from agentscope.domain.evaluation import (
    CaseResult,
    CheckName,
    CheckResult,
    CheckStats,
    CheckStatus,
    ReasonCode,
)
from agentscope.domain.traces import RunStatus, TokenUsage
from agentscope.evaluation.metrics import compute_metrics


def check(name: CheckName, status: CheckStatus) -> CheckResult:
    return CheckResult(name=name, status=status, reason=ReasonCode.OK, message="")


def result(case_id: str, passed: bool, *checks: CheckResult, **kw: Any) -> CaseResult:
    harness_error = kw.pop("harness_error", None)
    has_run = harness_error is None
    return CaseResult(
        case_id=case_id,
        case_hash=f"sha256:{case_id}",
        run_id=f"run-{case_id}" if has_run else None,
        trace_id=None,
        passed=passed,
        status=RunStatus.COMPLETED if has_run else None,
        harness_error=harness_error,
        checks=list(checks),
        **kw,
    )


PASS, FAIL, NA = CheckStatus.PASSED, CheckStatus.FAILED, CheckStatus.NOT_APPLICABLE
POLICY = CheckName.POLICY_COMPLIANCE


def test_pass_rate_counts_harness_errors_as_not_passed() -> None:
    metrics = compute_metrics(
        [
            result("a", True, steps=2, duration_ms=10),
            result("b", False, steps=4, duration_ms=30),
            result("c", False, harness_error="RuntimeError: boom"),
        ]
    )
    assert (metrics.total, metrics.passed, metrics.failed, metrics.harness_errors) == (3, 1, 1, 1)
    assert metrics.pass_rate == 1 / 3
    assert metrics.run_statuses == {"completed": 2}
    assert metrics.avg_steps == 3  # over cases that ran
    assert metrics.timings is not None
    assert metrics.timings.median_duration_ms == 20


def test_per_check_counts_and_policy_violations_count_cases() -> None:
    metrics = compute_metrics(
        [
            result("a", True, check(POLICY, PASS), check(CheckName.TOOL_ORDER, NA)),
            result(
                "b",
                False,
                check(POLICY, FAIL),
                check(CheckName.TOOL_ORDER, FAIL),
                unauthorized_tool_attempts=3,
                unauthorized_tool_executions=1,
            ),
            result("c", False, check(POLICY, FAIL), injected_fault_outcomes=2),
        ]
    )
    assert metrics.checks == {
        CheckName.TOOL_ORDER: CheckStats(passed=0, failed=1, not_applicable=1),
        POLICY: CheckStats(passed=1, failed=2, not_applicable=0),
    }
    assert list(metrics.checks) == [CheckName.TOOL_ORDER, POLICY]  # contract order
    assert metrics.policy_violation_cases == 2
    assert (metrics.unauthorized_tool_attempts, metrics.unauthorized_tool_executions) == (3, 1)
    assert (metrics.injected_fault_outcomes, metrics.unexpected_tool_errors) == (2, 0)


def test_usage_is_absent_when_no_run_reported_it() -> None:
    cases = [result("a", True), result("b", True)]
    assert compute_metrics(cases).usage is None
    assert compute_metrics(cases, {"run-a": None, "run-b": None}).usage is None


def test_usage_summary_is_partial_and_skips_incomplete_runs() -> None:
    complete = TokenUsage(input_tokens=100, output_tokens=20, reported_calls=2, total_calls=2)
    partial = TokenUsage(input_tokens=999, output_tokens=999, reported_calls=1, total_calls=2)
    cases = [result("a", True), result("b", True), result("c", True)]
    usage = compute_metrics(cases, {"run-a": complete, "run-b": partial, "run-c": None}).usage
    assert usage is not None
    assert (usage.input_tokens, usage.output_tokens) == (100, 20)
    assert (usage.reporting_runs, usage.total_runs) == (1, 3)


def test_empty_suite_has_zero_rates_not_errors() -> None:
    metrics = compute_metrics([])
    assert (metrics.total, metrics.pass_rate, metrics.avg_steps) == (0, 0.0, 0.0)
    assert metrics.timings is None
