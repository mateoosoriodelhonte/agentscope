"""Compares two suite results case by case.

Cases are matched by id. A case whose content changed between the two suites is a
different test that shares an id, so it is listed but never counted as a flip; neither is a
case where AgentScope itself failed on one side (a harness error has no agent verdict).
Deltas are absolute counts or percentage points over the compared cases (with their `n`),
never relative percentages, which exaggerate changes on small suites.

The configuration diff covers everything that can change behaviour, not only AgentConfig
fields: resolved content (prompt text, tool schemas, output schema, graph, scripted model)
when both suites recorded it, the AgentScope version and the packaged tool data. Only then
is `confounded` trustworthy.
"""

from __future__ import annotations

import uuid
from collections import Counter
from collections.abc import Iterator, Sequence
from datetime import datetime
from fractions import Fraction
from typing import Any, Literal

from agentscope.domain.agents import AgentConfig
from agentscope.domain.common import canonical_json, utcnow
from agentscope.domain.evaluation import CaseResult, CheckName, CheckStatus, SuiteResult
from agentscope.domain.experiments import (
    CaseChange,
    CaseComparison,
    CheckFlip,
    Comparison,
    ConfigDifference,
    ConfigDimension,
    MetricDelta,
    SuiteSummary,
)
from agentscope.evaluation.metrics import compute_metrics

# Top-level AgentConfig fields and the behaviour dimension each one belongs to.
DIMENSIONS: dict[str, ConfigDimension] = {
    "prompt_version": "prompt",
    "model": "model",
    "tool_schema_version": "tool_schema",
    "graph": "graph",
    "retry": "retry",
    "isolate_untrusted": "isolation",
    "tools": "tools",
    "limits": "limits",
}
# They name or describe a configuration but do not change its behaviour.
_IDENTITY_FIELDS = {"version", "label", "description"}

Pair = tuple[CaseResult, CaseResult]
_APPLICABLE = (CheckStatus.PASSED, CheckStatus.FAILED)


def _differences(a: Any, b: Any, path: str) -> Iterator[tuple[str, Any, Any]]:
    if isinstance(a, dict) and isinstance(b, dict):
        for key in sorted(a.keys() | b.keys()):
            yield from _differences(a.get(key), b.get(key), f"{path}.{key}" if path else key)
    elif canonical_json(a) != canonical_json(b):
        yield path, a, b


def config_diff(
    a: AgentConfig, b: AgentConfig, a_effective: str = "", b_effective: str = ""
) -> list[ConfigDifference]:
    """Dotted paths that differ, each tagged with its behaviour dimension. When the
    configurations are identical but their effective hashes differ (prompt text, tool
    schemas or the fake brain changed underneath), that is reported as `other`."""
    left = a.model_dump(mode="json", exclude=_IDENTITY_FIELDS)
    right = b.model_dump(mode="json", exclude=_IDENTITY_FIELDS)
    diffs = [
        ConfigDifference(
            path=path, dimension=DIMENSIONS.get(path.split(".")[0], "other"), a=va, b=vb
        )
        for path, va, vb in _differences(left, right, "")
    ]
    if not diffs and a_effective != b_effective:
        diffs.append(_effective_difference(a_effective, b_effective))
    return diffs


def _effective_difference(a_effective: str, b_effective: str) -> ConfigDifference:
    return ConfigDifference(
        path="effective_config_hash", dimension="other", a=a_effective, b=b_effective
    )


def resolved_diff(a: SuiteResult, b: SuiteResult) -> list[ConfigDifference]:
    """Resolved content that changed although the configuration fields naming it did not:
    edited prompt text under the same prompt version, a tool schema edited under the same
    tool schema version, another output schema or graph topology, a new scripted-model
    version. Changes the configuration explains (a new prompt version has new text, an added
    tool has a new schema) are already listed as configuration differences. Empty when
    either suite did not record its resolved content."""
    ra, rb = a.environment.resolved, b.environment.resolved
    if ra is None or rb is None:
        return []
    ca, cb = a.agent_config, b.agent_config
    diffs: list[ConfigDifference] = []

    def hidden(path: str, dimension: ConfigDimension, va: Any, vb: Any) -> None:
        diffs.append(ConfigDifference(path=f"resolved.{path}", dimension=dimension, a=va, b=vb))

    if ra.prompt != rb.prompt and ca.prompt_version == cb.prompt_version:
        hidden("prompt", "prompt", ra.prompt, rb.prompt)
    if ca.tool_schema_version == cb.tool_schema_version:
        for tool in sorted(ra.tools.keys() & rb.tools.keys()):
            if ra.tools[tool] != rb.tools[tool]:
                hidden(f"tools.{tool}", "tool_schema", ra.tools[tool], rb.tools[tool])
    if ra.output_schema != rb.output_schema and ca.agent == cb.agent:
        hidden("output_schema", "other", ra.output_schema, rb.output_schema)
    if ra.graph != rb.graph and ca.graph == cb.graph:
        hidden("graph", "graph", ra.graph, rb.graph)
    if None not in (ra.fake_brain_version, rb.fake_brain_version) and (
        ra.fake_brain_version != rb.fake_brain_version
    ):
        hidden("fake_brain_version", "model", ra.fake_brain_version, rb.fake_brain_version)
    return diffs


# Recorded on every suite and able to change behaviour without any configuration change:
# the AgentScope code (prompts, tools, runtime) and the packaged tool data.
_ENVIRONMENT_FIELDS = ("agentscope_version", "data_hash")


def environment_diff(a: SuiteResult, b: SuiteResult) -> list[ConfigDifference]:
    return [
        ConfigDifference(path=f"environment.{field}", dimension="other", a=va, b=vb)
        for field in _ENVIRONMENT_FIELDS
        if (va := getattr(a.environment, field)) != (vb := getattr(b.environment, field))
    ]


def suite_diff(a: SuiteResult, b: SuiteResult) -> tuple[list[ConfigDifference], list[str]]:
    """Every behaviour-relevant difference between two suites (configuration fields,
    resolved content, AgentScope version and tool data), plus warnings for what cannot be
    compared. The effective hash covers configuration and resolved content together, so it
    is listed only when nothing else accounts for its change."""
    configured = config_diff(a.agent_config, b.agent_config)
    resolved = resolved_diff(a, b)
    diffs = configured + resolved + environment_diff(a, b)
    if not configured and not resolved and a.effective_config_hash != b.effective_config_hash:
        diffs.append(_effective_difference(a.effective_config_hash, b.effective_config_hash))
    warnings: list[str] = []
    engines = (a.environment.check_engine_version, b.environment.check_engine_version)
    if engines[0] != engines[1]:
        warnings.append(
            f"verdicts come from different check engines ({engines[0]} vs {engines[1]}): "
            "flips may reflect changed checks, not changed behaviour"
        )
    unrecorded = [s.suite_id for s in (a, b) if s.environment.resolved is None]
    if configured and unrecorded:
        warnings.append(
            "resolved content (prompt text, tool schemas, output schema, graph, scripted "
            f"model) was not recorded for {', '.join(unrecorded)}, so changes beyond the "
            "listed configuration fields cannot be ruled out"
        )
    return diffs, warnings


def _failed_checks(case: CaseResult) -> list[str]:
    failed = [c.name.value for c in case.checks if c.status is CheckStatus.FAILED]
    return (["harness_error"] if case.harness_error is not None else []) + failed


def classify(a: CaseResult | None, b: CaseResult | None) -> CaseChange:
    """A harness error on either side means AgentScope, not the agent, failed there: there
    is no verdict to compare, so the case is neither a regression nor a fix."""
    if a is None:
        return CaseChange.ADDED
    if b is None:
        return CaseChange.REMOVED
    if a.case_hash != b.case_hash:
        return CaseChange.CONTENT_CHANGED
    if a.harness_error is not None or b.harness_error is not None:
        return CaseChange.HARNESS_ERROR
    if a.passed and b.passed:
        return CaseChange.STILL_PASSING
    if a.passed:
        return CaseChange.REGRESSION
    if b.passed:
        return CaseChange.FIX
    if _failed_checks(a) == _failed_checks(b):
        return CaseChange.STILL_FAILING_SAME
    return CaseChange.STILL_FAILING_CHANGED


def check_flips(pairs: Sequence[Pair]) -> list[CheckFlip]:
    regressions: Counter[CheckName] = Counter()
    fixes: Counter[CheckName] = Counter()
    seen: set[CheckName] = set()
    for a, b in pairs:
        before = {c.name: c.status for c in a.checks}
        for check in b.checks:
            if check.name not in before:
                continue
            seen.add(check.name)
            if before[check.name] is CheckStatus.PASSED and check.status is CheckStatus.FAILED:
                regressions[check.name] += 1
            elif before[check.name] is CheckStatus.FAILED and check.status is CheckStatus.PASSED:
                fixes[check.name] += 1
    return [
        CheckFlip(check=name.value, regressions=regressions[name], fixes=fixes[name])
        for name in CheckName
        if name in seen
    ]


def _pp(part: int, whole: int) -> Fraction:
    return Fraction(100 * part, whole) if whole else Fraction(0)


def _delta(
    metric: str,
    unit: Literal["count", "pp", "steps"],
    a: Fraction,
    b: Fraction,
    higher_is_better: bool,
    n: int,
) -> MetricDelta:
    # Computed exactly, then rounded once: 30 pp - 20 pp is 10.0, not 10.000000000000004.
    return MetricDelta(
        metric=metric,
        unit=unit,
        a=float(a),
        b=float(b),
        delta=float(b - a),
        higher_is_better=higher_is_better,
        n=n,
    )


def metric_deltas(pairs: Sequence[Pair]) -> list[MetricDelta]:
    """Deltas over the compared cases only, so both sides are measured on the same tests."""
    n = len(pairs)
    ma = compute_metrics([a for a, _ in pairs])
    mb = compute_metrics([b for _, b in pairs])
    deltas = [_delta("pass_rate", "pp", _pp(ma.passed, n), _pp(mb.passed, n), True, n)]
    counts = (
        ("passed", True),
        ("harness_errors", False),
        ("invalid_outputs", False),
        ("policy_violation_cases", False),
        ("unauthorized_tool_attempts", False),
        ("unauthorized_tool_executions", False),
        ("unexpected_tool_errors", False),
    )
    for metric, higher_is_better in counts:
        a_value, b_value = getattr(ma, metric), getattr(mb, metric)
        deltas.append(
            _delta(metric, "count", Fraction(a_value), Fraction(b_value), higher_is_better, n)
        )
    a_steps, b_steps = _avg_steps([a for a, _ in pairs]), _avg_steps([b for _, b in pairs])
    deltas.append(_delta("avg_steps", "steps", a_steps, b_steps, False, n))
    for name in CheckName:
        # Pass rate over the cases where the check applied on both sides (same n for both).
        statuses = [
            (sa, sb)
            for a, b in pairs
            if (sa := _status(a, name)) in _APPLICABLE and (sb := _status(b, name)) in _APPLICABLE
        ]
        if statuses:
            k = len(statuses)
            a_passed = sum(1 for sa, _ in statuses if sa is CheckStatus.PASSED)
            b_passed = sum(1 for _, sb in statuses if sb is CheckStatus.PASSED)
            deltas.append(
                _delta(f"check:{name.value}", "pp", _pp(a_passed, k), _pp(b_passed, k), True, k)
            )
    return deltas


def _avg_steps(cases: Sequence[CaseResult]) -> Fraction:
    """Mean steps over the cases that ran (as in SuiteMetrics), computed exactly."""
    ran = [c.steps for c in cases if c.run_id is not None]
    return Fraction(sum(ran), len(ran)) if ran else Fraction(0)


def _status(case: CaseResult, name: CheckName) -> CheckStatus | None:
    return next((c.status for c in case.checks if c.name is name), None)


def _summary(result: SuiteResult) -> SuiteSummary:
    return SuiteSummary(
        suite_id=result.suite_id,
        agent_ref=result.agent_config.ref,
        config_hash=result.config_hash,
        effective_config_hash=result.effective_config_hash,
        dataset_hash=result.dataset_hash,
        metrics=result.metrics,
    )


def compare(
    a: SuiteResult,
    b: SuiteResult,
    name: str,
    *,
    experiment_id: str | None = None,
    created_at: datetime | None = None,
) -> Comparison:
    """Compare suite `a` (reference) with suite `b` (candidate). With different dataset
    content, only cases present in both with unchanged content are compared."""
    a_cases = {c.case_id: c for c in a.cases}
    b_cases = {c.case_id: c for c in b.cases}
    cases: list[CaseComparison] = []
    # Metric deltas cover every compared case (a harness error counts as not passed, and
    # shows in the harness_errors delta); check flips only cases with a verdict on both sides.
    pairs: list[Pair] = []
    judged: list[Pair] = []
    for case_id in sorted(a_cases.keys() | b_cases.keys()):
        ca, cb = a_cases.get(case_id), b_cases.get(case_id)
        change = classify(ca, cb)
        if ca is not None and cb is not None and change is not CaseChange.CONTENT_CHANGED:
            pairs.append((ca, cb))
            if change is not CaseChange.HARNESS_ERROR:
                judged.append((ca, cb))
        cases.append(
            CaseComparison(
                case_id=case_id,
                change=change,
                a_passed=ca.passed if ca else None,
                b_passed=cb.passed if cb else None,
                a_failed_checks=_failed_checks(ca) if ca else [],
                b_failed_checks=_failed_checks(cb) if cb else [],
                a_run_id=ca.run_id if ca else None,
                b_run_id=cb.run_id if cb else None,
            )
        )
    diff, warnings = suite_diff(a, b)
    return Comparison(
        experiment_id=experiment_id or str(uuid.uuid4()),
        name=name,
        created_at=created_at or utcnow(),
        dataset_name=a.dataset_name,
        dataset_version=a.dataset_version,
        dataset_hash=a.dataset_hash,
        dataset_mismatch=a.dataset_hash != b.dataset_hash,
        deterministic=a.environment.deterministic and b.environment.deterministic,
        a=_summary(a),
        b=_summary(b),
        metric_deltas=metric_deltas(pairs),
        check_flips=check_flips(judged),
        cases=cases,
        config_diff=diff,
        confounded=len({d.dimension for d in diff}) > 1,
        warnings=warnings,
    )
