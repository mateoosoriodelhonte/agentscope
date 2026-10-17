"""Regression gates: every configured threshold becomes one rule with its actual value.

Rates and percentage-point drops use exact rational arithmetic. In floats, a drop from
4/10 to 3/10 is 10.000000000000004 pp and would fail a 10 pp limit. Thresholds are read
through `str()`, so `0.9` means exactly nine tenths, not the nearest binary float.
"""

from __future__ import annotations

from collections.abc import Sequence
from fractions import Fraction

from pydantic import JsonValue

from agentscope.domain.evaluation import (
    CaseResult,
    GateResult,
    GateRule,
    GateThresholds,
    SuiteResult,
)
from agentscope.evaluation.baseline import Baseline, ensure_compatible


def exact(value: float) -> Fraction:
    """The decimal a person wrote, not its nearest binary float."""
    return Fraction(str(value))


def _rate(passed: int, total: int) -> Fraction:
    return Fraction(passed, total) if total else Fraction(0)


def _display(value: Fraction) -> JsonValue:
    return value.numerator if value.denominator == 1 else round(float(value), 4)


def new_regressions(baseline: Baseline, cases: Sequence[CaseResult]) -> list[str]:
    """Case ids that passed in the baseline and do not pass now. Cases whose content
    changed are skipped: they are different tests that happen to share an id. So are
    harness errors: AgentScope failed, not the agent, and max_harness_errors judges them."""
    before = {c.case_id: c for c in baseline.cases}
    return sorted(
        c.case_id
        for c in cases
        if (b := before.get(c.case_id)) is not None
        and b.case_hash == c.case_hash
        and b.passed
        and not c.passed
        and c.harness_error is None
    )


def evaluate_gate(
    thresholds: GateThresholds, result: SuiteResult, baseline: Baseline | None = None
) -> GateResult:
    """Raises IncompatibleBaselineError when the baseline is not comparable."""
    if baseline is not None:
        ensure_compatible(
            baseline,
            dataset_hash=result.dataset_hash,
            checks_enabled=result.checks_enabled,
            check_engine_version=result.environment.check_engine_version,
        )
    m = result.metrics
    rate = _rate(m.passed, m.total)
    rules: list[GateRule] = []

    if thresholds.min_pass_rate is not None:
        rules.append(
            GateRule(
                name="min_pass_rate",
                threshold=thresholds.min_pass_rate,
                actual=f"{m.passed}/{m.total}",
                passed=m.total > 0 and rate >= exact(thresholds.min_pass_rate),
            )
        )
    if thresholds.max_new_regressions is not None:
        if baseline is None:
            rules.append(_needs_baseline("max_new_regressions", thresholds.max_new_regressions))
        else:
            flips = new_regressions(baseline, result.cases)
            rules.append(
                GateRule(
                    name="max_new_regressions",
                    threshold=thresholds.max_new_regressions,
                    actual=len(flips),
                    passed=len(flips) <= thresholds.max_new_regressions,
                    detail=("regressed: " + ", ".join(flips[:20])) if flips else "",
                )
            )
    if thresholds.max_pass_rate_drop_pp is not None:
        if baseline is None:
            rules.append(_needs_baseline("max_pass_rate_drop_pp", thresholds.max_pass_rate_drop_pp))
        else:
            b = baseline.metrics
            drop = (_rate(b.passed, b.total) - rate) * 100
            rules.append(
                GateRule(
                    name="max_pass_rate_drop_pp",
                    threshold=thresholds.max_pass_rate_drop_pp,
                    actual=_display(drop),
                    passed=drop <= thresholds.max_pass_rate_drop_pp,
                    detail=f"{b.passed}/{b.total} -> {m.passed}/{m.total}",
                )
            )
    counts = (
        ("max_invalid_outputs", thresholds.max_invalid_outputs, m.invalid_outputs),
        (
            "max_policy_violation_cases",
            thresholds.max_policy_violation_cases,
            m.policy_violation_cases,
        ),
        (
            "max_unauthorized_tool_executions",
            thresholds.max_unauthorized_tool_executions,
            m.unauthorized_tool_executions,
        ),
        ("max_harness_errors", thresholds.max_harness_errors, m.harness_errors),
    )
    for name, limit, actual in counts:
        if limit is not None:
            rules.append(
                GateRule(name=name, threshold=limit, actual=actual, passed=actual <= limit)
            )
    for check in dict.fromkeys(thresholds.required_checks):
        stats = m.checks.get(check)
        failed = stats.failed if stats else 0
        applicable = stats.applicable if stats else 0
        rules.append(
            GateRule(
                name=f"required_check:{check.value}",
                threshold={"max_failed": 0, "min_applicable": 1},
                actual={"failed": failed, "applicable": applicable},
                passed=failed == 0 and applicable >= 1,
                detail="" if applicable else "never applied (not enabled, or no case judges it)",
            )
        )
    return GateResult(
        passed=all(r.passed for r in rules), rules=rules, baseline_used=baseline is not None
    )


def _needs_baseline(name: str, threshold: int) -> GateRule:
    """A configured comparison without a baseline cannot be verified, so it fails."""
    return GateRule(
        name=name, threshold=threshold, actual=None, passed=False, detail="requires a baseline"
    )
