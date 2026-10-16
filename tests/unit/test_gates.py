"""Regression gates: exact arithmetic, per-case regressions, required checks, baselines."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from agentscope.agents.research import VERSIONS
from agentscope.domain.evaluation import (
    CHECK_ENGINE_VERSION,
    CaseResult,
    CheckName,
    CheckResult,
    CheckStatus,
    EnvironmentFingerprint,
    GateRule,
    GateThresholds,
    ReasonCode,
    SuiteResult,
)
from agentscope.evaluation.baseline import Baseline, IncompatibleBaselineError, build_baseline
from agentscope.evaluation.gates import evaluate_gate, exact
from agentscope.evaluation.metrics import compute_metrics

T0 = datetime(2026, 1, 1, tzinfo=UTC)
CONFIG = VERSIONS[1]


def case(case_id: str, passed: bool, *, case_hash: str | None = None, **checks: str) -> CaseResult:
    return CaseResult(
        case_id=case_id,
        case_hash=case_hash or f"sha256:{case_id}",
        run_id=f"run-{case_id}",
        trace_id=None,
        passed=passed,
        status=None,
        checks=[
            CheckResult(
                name=CheckName(name), status=CheckStatus(status), reason=ReasonCode.OK, message=""
            )
            for name, status in checks.items()
        ],
    )


def suite(cases: list[CaseResult], dataset_hash: str = "sha256:dataset") -> SuiteResult:
    return SuiteResult(
        suite_id="suite",
        dataset_name="gates",
        dataset_version="1",
        dataset_hash=dataset_hash,
        agent_config=CONFIG,
        config_hash=CONFIG.config_hash(),
        effective_config_hash="sha256:effective",
        checks_enabled=sorted(CheckName),
        environment=EnvironmentFingerprint(
            agentscope_version="0.1.0",
            check_engine_version=CHECK_ENGINE_VERSION,
            python_version="3.12.0",
            langgraph_version="1.2.0",
            data_hash="sha256:data",
            provider="fake",
            model="scripted-naive",
            deterministic=True,
        ),
        started_at=T0,
        finished_at=T0,
        cases=cases,
        metrics=compute_metrics(cases),
    )


def passing(n: int, failing: int) -> SuiteResult:
    return suite(
        [case(f"p{i}", True) for i in range(n)] + [case(f"f{i}", False) for i in range(failing)]
    )


def rule(
    result: SuiteResult,
    thresholds: GateThresholds,
    name: str,
    baseline: Baseline | None = None,
) -> GateRule:
    gate = evaluate_gate(thresholds, result, baseline)
    return next(r for r in gate.rules if r.name == name)


def test_thresholds_mean_the_decimal_that_was_written() -> None:
    assert exact(0.9) * 10 == 9
    assert exact(0.1) + exact(0.2) == exact(0.3)


@pytest.mark.parametrize(
    ("passed", "failed", "threshold", "ok"),
    [(9, 1, 0.9, True), (8, 2, 0.9, False), (7, 3, 0.7, True), (1, 2, 0.3333333333333333, True)],
)
def test_min_pass_rate_is_compared_exactly(
    passed: int, failed: int, threshold: float, ok: bool
) -> None:
    result = rule(passing(passed, failed), GateThresholds(min_pass_rate=threshold), "min_pass_rate")
    assert (result.threshold, result.actual, result.passed) == (
        threshold,
        f"{passed}/{passed + failed}",
        ok,
    )


def test_pass_rate_drop_is_exact_where_floats_are_not() -> None:
    # In floats, (4/10 - 3/10) * 100 == 10.000000000000004 and would fail a 10 pp limit.
    assert (4 / 10 - 3 / 10) * 100 > 10
    baseline = build_baseline(passing(4, 6))
    current = passing(3, 7)
    thresholds = GateThresholds(max_pass_rate_drop_pp=10)
    drop = rule(current, thresholds, "max_pass_rate_drop_pp", baseline=baseline)
    assert (drop.actual, drop.passed, drop.detail) == (10, True, "4/10 -> 3/10")
    worse = rule(passing(2, 8), thresholds, "max_pass_rate_drop_pp", baseline=baseline)
    assert (worse.actual, worse.passed) == (20, False)
    better = rule(passing(9, 1), thresholds, "max_pass_rate_drop_pp", baseline=baseline)
    assert (better.actual, better.passed) == (-50, True)


def test_new_regressions_are_per_case_and_skip_changed_content() -> None:
    before = suite(
        [
            case("a", True),
            case("b", True),
            case("c", False),
            case("d", True, case_hash="sha256:old-d"),
        ]
    )
    after = suite([case("a", False), case("b", True), case("c", True), case("d", False)])
    regressions = rule(
        after,
        GateThresholds(max_new_regressions=0),
        "max_new_regressions",
        baseline=build_baseline(before),
    )
    assert (regressions.actual, regressions.passed, regressions.detail) == (
        1,
        False,
        "regressed: a",
    )


def test_baseline_rules_without_a_baseline_fail_loudly() -> None:
    gate = evaluate_gate(
        GateThresholds(max_new_regressions=0, max_pass_rate_drop_pp=5), passing(1, 0)
    )
    assert not gate.passed
    assert not gate.baseline_used
    failed = [(r.name, r.detail) for r in gate.rules if not r.passed]
    assert failed == [
        ("max_new_regressions", "requires a baseline"),
        ("max_pass_rate_drop_pp", "requires a baseline"),
    ]


def test_required_checks_need_zero_failures_and_at_least_one_application() -> None:
    result = suite(
        [
            case("a", True, tool_order="passed", schema_validity="passed"),
            case("b", False, tool_order="not_applicable", schema_validity="failed"),
        ]
    )
    thresholds = GateThresholds(
        required_checks=[
            CheckName.TOOL_ORDER,
            CheckName.SCHEMA_VALIDITY,
            CheckName.TIMEOUT_BEHAVIOR,
            CheckName.TOOL_ORDER,
        ]
    )
    gate = evaluate_gate(thresholds, result)
    required = {r.name: (r.actual, r.passed) for r in gate.rules if r.name.startswith("required")}
    assert required == {
        "required_check:tool_order": ({"failed": 0, "applicable": 1}, True),
        "required_check:schema_validity": ({"failed": 1, "applicable": 2}, False),
        "required_check:timeout_behavior": ({"failed": 0, "applicable": 0}, False),
    }


def test_default_gate_has_safety_rules_and_one_rule_per_threshold() -> None:
    gate = evaluate_gate(GateThresholds(), passing(2, 1))
    assert [(r.name, r.threshold, r.actual) for r in gate.rules] == [
        ("max_unauthorized_tool_executions", 0, 0),
        ("max_harness_errors", 0, 0),
    ]
    assert gate.passed  # thresholds that are not configured do not judge


def test_count_thresholds() -> None:
    cases = [case("a", False, policy_compliance="failed"), case("b", False)]
    cases[1] = cases[1].model_copy(update={"invalid_output": True, "harness_error": "boom"})
    gate = evaluate_gate(
        GateThresholds(max_invalid_outputs=0, max_policy_violation_cases=1, max_harness_errors=1),
        suite(cases),
    )
    assert {r.name: (r.actual, r.passed) for r in gate.rules} == {
        "max_invalid_outputs": (1, False),
        "max_policy_violation_cases": (1, True),
        "max_unauthorized_tool_executions": (0, True),
        "max_harness_errors": (1, True),
    }


def test_incompatible_baseline_is_a_typed_error() -> None:
    baseline = build_baseline(suite([case("a", True)], dataset_hash="sha256:other"))
    with pytest.raises(IncompatibleBaselineError, match="dataset hash"):
        evaluate_gate(GateThresholds(), suite([case("a", True)]), baseline)


def test_compatible_baseline_is_marked_used() -> None:
    result = passing(3, 0)
    gate = evaluate_gate(GateThresholds(max_new_regressions=0), result, build_baseline(result))
    assert gate.passed
    assert gate.baseline_used
