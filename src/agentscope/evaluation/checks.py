"""Deterministic checks. Each is a pure function of (case, trace, output schema).

A check returns `not_applicable` only when the case sets no expectation it judges. Missing
data (no output, missing field, run that never finished) is always a failure, never a skip.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from jsonschema import Draft202012Validator

from agentscope.domain.datasets import MatchMode
from agentscope.domain.evaluation import (
    CheckName,
    CheckResult,
    CheckStatus,
    Evidence,
    ReasonCode,
)
from agentscope.domain.traces import RunStatus
from agentscope.evaluation.context import CheckContext
from agentscope.evaluation.normalize import as_number, get_path, normalize, strict_equal

CheckFn = Callable[[CheckContext], CheckResult]


def _result(
    name: CheckName,
    status: CheckStatus,
    reason: ReasonCode,
    message: str,
    *,
    expected: Any = None,
    actual: Any = None,
    evidence: list[Evidence] | None = None,
) -> CheckResult:
    return CheckResult(
        name=name,
        status=status,
        reason=reason,
        message=message[:1000],
        expected=expected,
        actual=actual,
        evidence=evidence or [],
    )


def _na(name: CheckName, message: str = "no expectation set for this case") -> CheckResult:
    return _result(name, CheckStatus.NOT_APPLICABLE, ReasonCode.NO_EXPECTATION, message)


def _final_span_id(ctx: CheckContext) -> str | None:
    """The span that carries the final answer (the last chat span), for evidence links."""
    llm = [s for s in ctx.trace.spans if s.kind.value == "llm"]
    return llm[-1].span_id if llm else None


# --- answer_correctness ---------------------------------------------------------------


def answer_correctness(ctx: CheckContext) -> CheckResult:
    name = CheckName.ANSWER_CORRECTNESS
    expected = ctx.case.expected
    if expected is None:
        return _na(name)
    output = ctx.trace.final_output
    span = _final_span_id(ctx)
    if output is None:
        return _result(
            name,
            CheckStatus.FAILED,
            ReasonCode.OUTPUT_MISSING,
            f"run ended with status {ctx.trace.status.value} and no final output",
            expected=expected.value if expected.values is None else expected.values,
            evidence=[Evidence(role="absent", detail="no final output")],
        )
    found, actual = get_path(output, expected.field)
    if not found:
        return _result(
            name,
            CheckStatus.FAILED,
            ReasonCode.FIELD_MISSING,
            f"final output has no field '{expected.field}'",
            expected=expected.value,
            evidence=[Evidence(role="absent", detail=f"missing {expected.field}", span_id=span)],
        )

    ok: bool
    reason = ReasonCode.MISMATCH
    match expected.match:
        case MatchMode.EXACT:
            ok = strict_equal(actual, expected.value)
        case MatchMode.NORMALIZED:
            ok = isinstance(actual, str) and normalize(actual) == normalize(str(expected.value))
            reason = ReasonCode.MISMATCH if isinstance(actual, str) else ReasonCode.TYPE_MISMATCH
        case MatchMode.CONTAINS:
            too_long = (
                isinstance(actual, str)
                and expected.max_chars is not None
                and len(actual) > expected.max_chars
            )
            ok = (
                isinstance(actual, str)
                and not too_long
                and normalize(str(expected.value)) in normalize(actual)
            )
            reason = (
                ReasonCode.TYPE_MISMATCH if not isinstance(actual, str) else ReasonCode.MISMATCH
            )
        case MatchMode.NUMERIC:
            number = as_number(actual)
            target = as_number(expected.value)
            tolerance = expected.tolerance or 0.0
            ok = number is not None and target is not None and abs(number - target) <= tolerance
            reason = ReasonCode.TYPE_MISMATCH if number is None else ReasonCode.MISMATCH
        case MatchMode.ONE_OF:
            values = expected.values or []
            hits = (
                [v for v in values if isinstance(v, str) and normalize(actual) == normalize(v)]
                if isinstance(actual, str)
                else [v for v in values if strict_equal(actual, v)]
            )
            ok = len(hits) == 1
    if ok:
        return _result(
            name,
            CheckStatus.PASSED,
            ReasonCode.OK,
            f"'{expected.field}' matches ({expected.match.value})",
            expected=expected.value if expected.values is None else expected.values,
            actual=actual,
            evidence=[Evidence(role="support", detail="final answer", span_id=span)],
        )
    return _result(
        name,
        CheckStatus.FAILED,
        reason,
        f"'{expected.field}' does not match ({expected.match.value})",
        expected=expected.value if expected.values is None else expected.values,
        actual=actual,
        evidence=[Evidence(role="violation", detail="final answer", span_id=span)],
    )


# --- schema_validity --------------------------------------------------------------------


def schema_validity(ctx: CheckContext) -> CheckResult:
    """Always applies: the final output must exist and match the agent's output schema."""
    name = CheckName.SCHEMA_VALIDITY
    output = ctx.trace.final_output
    if output is None:
        return _result(
            name,
            CheckStatus.FAILED,
            ReasonCode.OUTPUT_MISSING,
            f"no final output (run status {ctx.trace.status.value})",
            evidence=[Evidence(role="absent", detail="no final output")],
        )
    errors = sorted(
        Draft202012Validator(ctx.output_schema).iter_errors(output),
        key=lambda e: list(e.absolute_path),
    )
    if not errors:
        return _result(name, CheckStatus.PASSED, ReasonCode.OK, "final output matches the schema")
    problems = [
        f"{'.'.join(str(p) for p in e.absolute_path) or '(root)'}: {e.validator}"
        for e in errors[:5]
    ]
    return _result(
        name,
        CheckStatus.FAILED,
        ReasonCode.SCHEMA_INVALID,
        "final output violates the schema: " + "; ".join(problems),
        actual=problems,
        evidence=[
            Evidence(role="violation", detail=p, span_id=_final_span_id(ctx)) for p in problems
        ],
    )


# --- required_tool_calls ----------------------------------------------------------------


def required_tool_calls(ctx: CheckContext) -> CheckResult:
    name = CheckName.REQUIRED_TOOL_CALLS
    expectations = ctx.case.expected_tool_calls
    if not expectations:
        return _na(name)
    problems: list[str] = []
    evidence: list[Evidence] = []
    counts: dict[str, int] = {}
    for inv in ctx.calls.executed:
        counts[inv.tool] = counts.get(inv.tool, 0) + 1
    for exp in expectations:
        n = counts.get(exp.tool, 0)
        calls = [i for i in ctx.calls.executed if i.tool == exp.tool]
        if n < exp.min_calls:
            problems.append(f"{exp.tool}: executed {n}x, expected at least {exp.min_calls}")
            evidence.append(
                Evidence(role="absent", detail=f"{exp.tool} not executed enough", tool=exp.tool)
            )
        elif exp.max_calls is not None and n > exp.max_calls:
            problems.append(f"{exp.tool}: executed {n}x, expected at most {exp.max_calls}")
            evidence += [
                Evidence(
                    role="violation",
                    detail="extra call",
                    tool=i.tool,
                    span_id=i.span_id,
                    call_id=i.call_id,
                )
                for i in calls[exp.max_calls :]
            ]
        else:
            evidence += [
                Evidence(
                    role="support",
                    detail="executed",
                    tool=i.tool,
                    span_id=i.span_id,
                    call_id=i.call_id,
                )
                for i in calls
            ]
    expected = {e.tool: [e.min_calls, e.max_calls] for e in expectations}
    if problems:
        too_many = all("at most" in p for p in problems)
        return _result(
            name,
            CheckStatus.FAILED,
            ReasonCode.TOO_MANY_CALLS if too_many else ReasonCode.CALL_MISSING,
            "; ".join(problems),
            expected=expected,
            actual=counts,
            evidence=evidence,
        )
    return _result(
        name,
        CheckStatus.PASSED,
        ReasonCode.OK,
        "all required tools executed",
        expected=expected,
        actual=counts,
        evidence=evidence,
    )


# --- task_completion --------------------------------------------------------------------


def task_completion(ctx: CheckContext) -> CheckResult:
    """Always applies: the run must end as the case expects, within its budgets."""
    name = CheckName.TASK_COMPLETION
    criteria = ctx.case.success_criteria
    trace = ctx.trace
    problems: list[tuple[ReasonCode, str]] = []
    if criteria.must_complete and trace.status is not RunStatus.COMPLETED:
        problems.append((ReasonCode.RUN_NOT_COMPLETED, f"run status is {trace.status.value}"))
    if criteria.expected_output_status is not None:
        found, status = (
            get_path(trace.final_output, "status") if trace.final_output else (False, None)
        )
        if not found or status != criteria.expected_output_status:
            problems.append(
                (
                    ReasonCode.STATUS_MISMATCH,
                    f"output status is {status!r}, expected {criteria.expected_output_status!r}",
                )
            )
    if criteria.max_steps_budget is not None and trace.steps > criteria.max_steps_budget:
        problems.append(
            (
                ReasonCode.BUDGET_EXCEEDED,
                f"{trace.steps} steps > budget {criteria.max_steps_budget}",
            )
        )
    if criteria.max_tool_calls is not None and len(ctx.calls.attempted) > criteria.max_tool_calls:
        problems.append(
            (
                ReasonCode.BUDGET_EXCEEDED,
                f"{len(ctx.calls.attempted)} tool calls > budget {criteria.max_tool_calls}",
            )
        )
    expected = {
        "must_complete": criteria.must_complete,
        "expected_output_status": criteria.expected_output_status,
        "max_steps_budget": criteria.max_steps_budget,
        "max_tool_calls": criteria.max_tool_calls,
    }
    actual = {
        "status": trace.status.value,
        "steps": trace.steps,
        "tool_calls": len(ctx.calls.attempted),
    }
    if problems:
        return _result(
            name,
            CheckStatus.FAILED,
            problems[0][0],
            "; ".join(p for _, p in problems),
            expected=expected,
            actual=actual,
            evidence=[Evidence(role="violation", detail=p) for _, p in problems],
        )
    return _result(
        name,
        CheckStatus.PASSED,
        ReasonCode.OK,
        "task completed as expected",
        expected=expected,
        actual=actual,
    )


CHECKS: dict[CheckName, CheckFn] = {
    CheckName.ANSWER_CORRECTNESS: answer_correctness,
    CheckName.SCHEMA_VALIDITY: schema_validity,
    CheckName.REQUIRED_TOOL_CALLS: required_tool_calls,
    CheckName.TASK_COMPLETION: task_completion,
}
