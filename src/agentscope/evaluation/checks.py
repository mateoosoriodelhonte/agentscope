"""Deterministic checks. Each is a pure function of (case, trace, output schema).

A check returns `not_applicable` only when the case sets no expectation it judges. Missing
data (no output, missing field, run that never finished) is always a failure, never a skip.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import Any, Literal

from jsonschema import Draft202012Validator

from agentscope.domain.datasets import DEGRADED_STATUSES, ArgMatcher, MatchMode
from agentscope.domain.evaluation import (
    CheckName,
    CheckResult,
    CheckStatus,
    Evidence,
    EvidenceRole,
    ReasonCode,
)
from agentscope.domain.tools import FAILURE_FAULTS, FaultKind, ToolOutcome
from agentscope.domain.traces import RunStatus, ToolAttempt, ToolInvocation
from agentscope.evaluation.context import CheckContext
from agentscope.evaluation.leaks import Needle, find_encoding, string_leaves
from agentscope.evaluation.matchers import argument_matches, matcher_kind
from agentscope.evaluation.normalize import (
    as_number,
    contains_normalized,
    equal_normalized,
    get_path,
    strict_equal,
)

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
        evidence=(evidence or [])[:50],  # the contract caps evidence per check
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
    target = expected.value if expected.values is None else expected.values
    output = ctx.trace.final_output
    span = _final_span_id(ctx)
    if output is None:
        return _result(
            name,
            CheckStatus.FAILED,
            ReasonCode.OUTPUT_MISSING,
            f"run ended with status {ctx.trace.status.value} and no final output",
            expected=target,
            evidence=[Evidence(role="absent", detail="no final output")],
        )
    found, actual = get_path(output, expected.field)
    if not found:
        return _result(
            name,
            CheckStatus.FAILED,
            ReasonCode.FIELD_MISSING,
            f"final output has no field '{expected.field}'",
            expected=target,
            evidence=[Evidence(role="absent", detail=f"missing {expected.field}", span_id=span)],
        )

    ok: bool
    reason = ReasonCode.MISMATCH
    match expected.match:
        case MatchMode.EXACT:
            ok = strict_equal(actual, expected.value)
        case MatchMode.NORMALIZED:
            ok = isinstance(actual, str) and equal_normalized(actual, str(expected.value))
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
                and contains_normalized(str(expected.value), actual)
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
                [v for v in values if isinstance(v, str) and equal_normalized(actual, v)]
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
            expected=target,
            actual=actual,
            evidence=[Evidence(role="support", detail="final answer", span_id=span)],
        )
    return _result(
        name,
        CheckStatus.FAILED,
        reason,
        f"'{expected.field}' does not match ({expected.match.value})",
        expected=target,
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


# --- forbidden_tool_calls ---------------------------------------------------------------


def forbidden_tool_calls(ctx: CheckContext) -> CheckResult:
    """Always applies: a forbidden tool must not even be attempted. Attempts the runtime
    blocked still count, because the guard is not the evaluator."""
    name = CheckName.FORBIDDEN_TOOL_CALLS
    forbidden = sorted(set(ctx.case.forbidden_tools))
    hits = [i for i in ctx.calls.attempted if i.tool in forbidden]
    expected = {"forbidden": forbidden}
    attempted = sorted({i.tool for i in hits})
    actual = {"attempted": attempted}
    if hits:
        return _result(
            name,
            CheckStatus.FAILED,
            ReasonCode.FORBIDDEN_ATTEMPTED,
            f"forbidden tools attempted: {', '.join(attempted)}",
            expected=expected,
            actual=actual,
            evidence=[
                _call_evidence(
                    "violation",
                    "forbidden tool executed"
                    if i.executed
                    else "forbidden tool attempted (blocked by the runtime)",
                    i,
                )
                for i in hits
            ],
        )
    message = "no forbidden tool attempted" if forbidden else "no forbidden tools configured"
    return _result(
        name, CheckStatus.PASSED, ReasonCode.OK, message, expected=expected, actual=actual
    )


# --- tool_order -------------------------------------------------------------------------


def tool_order(ctx: CheckContext) -> CheckResult:
    """Greedy subsequence match over executed calls (a runtime retry is one call).

    Strict mode forbids other tools between consecutive ordered items; repeating the tool
    just matched (e.g. a second search before reading) is not "another tool".
    """
    name = CheckName.TOOL_ORDER
    order = ctx.case.tool_order
    if not order:
        return _na(name)
    strict = ctx.case.tool_order_strict
    executed = ctx.calls.executed
    matched: list[ToolInvocation] = []
    intruders: list[ToolInvocation] = []
    since_match: list[ToolInvocation] = []
    for inv in executed:
        if len(matched) == len(order):
            break
        if inv.tool == order[len(matched)]:
            intruders += since_match
            matched.append(inv)
            since_match = []
        elif matched and inv.tool != matched[-1].tool:
            since_match.append(inv)

    expected = {"order": list(order), "strict": strict}
    actual = {"executed": [i.tool for i in executed]}
    evidence = [
        _call_evidence("support", f"ordered item {k + 1}", i) for k, i in enumerate(matched)
    ]
    missing = order[len(matched) :]
    if missing:
        next_tool = missing[0]
        early = [i for i in executed if i.tool == next_tool]
        evidence += [
            Evidence(role="absent", detail=f"{t} not executed in order", tool=t) for t in missing
        ]
        evidence += [_call_evidence("violation", "executed out of order", i) for i in early]
        return _result(
            name,
            CheckStatus.FAILED,
            ReasonCode.ORDER_VIOLATED if early else ReasonCode.CALL_MISSING,
            f"expected order {' -> '.join(order)}; matched {len(matched)} of {len(order)}, "
            f"next expected {next_tool} " + ("ran out of order" if early else "never executed"),
            expected=expected,
            actual=actual,
            evidence=evidence,
        )
    if strict and intruders:
        return _result(
            name,
            CheckStatus.FAILED,
            ReasonCode.ORDER_VIOLATED,
            "other tools executed between ordered tools: "
            + ", ".join(sorted({i.tool for i in intruders})),
            expected=expected,
            actual=actual,
            evidence=evidence
            + [_call_evidence("violation", "executed between ordered tools", i) for i in intruders],
        )
    return _result(
        name,
        CheckStatus.PASSED,
        ReasonCode.OK,
        f"tools executed in order{' (strict)' if strict else ''}",
        expected=expected,
        actual=actual,
        evidence=evidence,
    )


# --- tool_arguments ---------------------------------------------------------------------


def tool_arguments(ctx: CheckContext) -> CheckResult:
    """Same-call binding: one executed call must satisfy all of an expectation's matchers,
    so matchers cannot be satisfied piecemeal by different calls."""
    name = CheckName.TOOL_ARGUMENTS
    expectations = [e for e in ctx.case.expected_tool_calls if e.arguments]
    if not expectations:
        return _na(name)
    problems: list[tuple[ReasonCode, str]] = []
    evidence: list[Evidence] = []
    for exp in expectations:
        calls = [i for i in ctx.calls.executed if i.tool == exp.tool]
        if not calls:
            problems.append((ReasonCode.CALL_MISSING, f"{exp.tool}: no executed call to check"))
            evidence.append(
                Evidence(role="absent", detail=f"no executed {exp.tool} call", tool=exp.tool)
            )
            continue
        # Paired with each call (not keyed by call_id, which a foreign trace may repeat).
        unmet = [
            (i, [a for a, m in exp.arguments.items() if not argument_matches(m, i.arguments, a)])
            for i in calls
        ]
        match = next((i for i, missing in unmet if not missing), None)
        if match is not None:
            evidence.append(_call_evidence("support", "all argument matchers satisfied", match))
            continue
        problems.append(
            (ReasonCode.ARGUMENTS_MISMATCH, f"{exp.tool}: no single call satisfies all matchers")
        )
        evidence += [
            _call_evidence(
                "violation",
                "unmet: " + ", ".join(f"{a} ({matcher_kind(exp.arguments[a])})" for a in missing),
                i,
            )
            for i, missing in unmet
        ]
    tools = {e.tool for e in expectations}
    expected = [
        {
            "tool": e.tool,
            "arguments": {a: {matcher_kind(m): _matcher_value(m)} for a, m in e.arguments.items()},
        }
        for e in expectations
    ]
    actual = [
        {"tool": i.tool, "call_id": i.call_id, "arguments": i.arguments}
        for i in ctx.calls.executed
        if i.tool in tools
    ][:20]
    if problems:
        mismatch = any(r is ReasonCode.ARGUMENTS_MISMATCH for r, _ in problems)
        return _result(
            name,
            CheckStatus.FAILED,
            ReasonCode.ARGUMENTS_MISMATCH if mismatch else ReasonCode.CALL_MISSING,
            "; ".join(p for _, p in problems),
            expected=expected,
            actual=actual,
            evidence=evidence,
        )
    return _result(
        name,
        CheckStatus.PASSED,
        ReasonCode.OK,
        "every expected call's arguments matched by a single call",
        expected=expected,
        actual=actual,
        evidence=evidence,
    )


def _matcher_value(matcher: ArgMatcher) -> Any:
    return getattr(matcher, matcher_kind(matcher))


# --- timeout_behavior -------------------------------------------------------------------


def timeout_behavior(ctx: CheckContext) -> CheckResult:
    """With an injected tool timeout: the timeout surfaced as a `timeout` outcome on that
    tool, retries stayed within budget, and the run still ended on its own terms. Durations
    are never asserted (they are not deterministic)."""
    name = CheckName.TIMEOUT_BEHAVIOR
    tools = sorted({f.tool for f in ctx.case.faults if f.kind is FaultKind.TIMEOUT})
    if not tools:
        return _na(name)
    trace = ctx.trace
    max_attempts = trace.config.retry.max_tool_retries + 1
    problems: list[tuple[ReasonCode, str]] = []
    evidence: list[Evidence] = []
    timeouts: dict[str, int] = {}
    most_attempts: dict[str, int] = {}
    for tool in tools:
        calls = [i for i in ctx.calls.executed if i.tool == tool]
        timed_out = [
            (i, a) for i in calls for a in _attempts(i) if a.outcome is ToolOutcome.TIMEOUT
        ]
        timeouts[tool] = len(timed_out)
        most_attempts[tool] = max((len(_attempts(i)) for i in calls), default=0)
        if timed_out:
            evidence += [_attempt_evidence("support", "timed out", i, a) for i, a in timed_out]
        else:
            problems.append(
                (
                    ReasonCode.TIMEOUT_NOT_SURFACED,
                    f"{tool}: the injected timeout never surfaced as a timeout outcome",
                )
            )
            evidence.append(Evidence(role="absent", detail=f"no {tool} timeout", tool=tool))
        for inv in calls:
            attempts = len(_attempts(inv))
            if attempts > max_attempts:
                problems.append(
                    (
                        ReasonCode.TOO_MANY_CALLS,
                        f"{tool}: {attempts} attempts in one call, budget {max_attempts}",
                    )
                )
                evidence.append(_call_evidence("violation", "retry budget exceeded", inv))
    if trace.status in (RunStatus.ERROR, RunStatus.TIMEOUT):
        problems.append(
            (ReasonCode.RUN_NOT_COMPLETED, f"run ended with status {trace.status.value}")
        )
        evidence.append(Evidence(role="violation", detail=f"run status {trace.status.value}"))
    expected = {
        "tools": tools,
        "max_attempts_per_call": max_attempts,
        "run_status_not_in": [RunStatus.ERROR.value, RunStatus.TIMEOUT.value],
    }
    actual = {"timeouts": timeouts, "max_attempts": most_attempts, "status": trace.status.value}
    if problems:
        return _result(
            name,
            CheckStatus.FAILED,
            problems[0][0],
            "; ".join(p for _, p in problems),
            expected=expected,
            actual=actual,
            evidence=evidence,
        )
    return _result(
        name,
        CheckStatus.PASSED,
        ReasonCode.OK,
        "timeout surfaced as a tool outcome and was handled within budget",
        expected=expected,
        actual=actual,
        evidence=evidence,
    )


# --- error_recovery ---------------------------------------------------------------------

# Injected faults that make a tool call fail, plus `incomplete` (it returns too little).
RECOVERY_FAULTS = FAILURE_FAULTS | {FaultKind.INCOMPLETE}
ANSWER_STATUSES = frozenset({"answered", "planned"})
RecoveryPath = Literal[
    "recovered", "degraded", "fabricated", "no_failure", "no_output", "run_error", "unresolved"
]
_RECOVERY_MESSAGES: dict[RecoveryPath, str] = {
    "run_error": "run ended with status error",
    "no_output": "no final output with a status",
    "fabricated": "answered after a tool failure without recovering (fabrication)",
    "no_failure": "the injected fault never surfaced as a failed tool attempt, so recovery "
    "could not be judged",
    "unresolved": "the output declines but still carries an answer, or has an unknown status",
    "recovered": "recovered, but the case expects graceful degradation",
    "degraded": "degraded, but the case expects recovery",
}


def _failed_attempt(attempt: ToolAttempt) -> bool:
    return attempt.outcome is not ToolOutcome.OK or attempt.fault in RECOVERY_FAULTS


def error_recovery(ctx: CheckContext) -> CheckResult:
    """After a tool failure the agent either recovered (a later clean success of the same
    or another permitted tool, then an answer) or degraded (a declining status and no
    answer). Answering without recovering is fabrication and always fails."""
    name = CheckName.ERROR_RECOVERY
    expect = ctx.case.success_criteria.expect_recovery
    if expect is None and not any(f.kind in RECOVERY_FAULTS for f in ctx.case.faults):
        return _na(name)
    trace = ctx.trace
    permitted = set(trace.permitted_tools)
    timeline = [(inv, a) for inv in ctx.calls.executed for a in _attempts(inv)]
    first = next((k for k, (_, a) in enumerate(timeline) if _failed_attempt(a)), None)
    failed = timeline[first] if first is not None else None
    recovery: tuple[ToolInvocation, ToolAttempt] | None = None
    if first is not None:
        failed_tool = timeline[first][0].tool
        recovery = next(
            (
                (inv, a)
                for inv, a in timeline[first + 1 :]
                if not _failed_attempt(a) and (inv.tool == failed_tool or inv.tool in permitted)
            ),
            None,
        )

    output = trace.final_output
    status = output.get("status") if isinstance(output, dict) else None
    answer = output.get("answer") if isinstance(output, dict) else None
    has_answer = answer is not None and not (isinstance(answer, str) and not answer.strip())
    path: RecoveryPath
    if trace.status is RunStatus.ERROR:
        path = "run_error"
    elif not isinstance(status, str):
        path = "no_output"
    elif recovery is not None and status in ANSWER_STATUSES:
        path = "recovered"
    elif status in DEGRADED_STATUSES and not has_answer:
        path = "degraded"
    elif status in ANSWER_STATUSES:
        path = "fabricated" if failed is not None else "no_failure"
    else:
        path = "unresolved"

    accepted: list[RecoveryPath]
    if expect is None:
        accepted = ["recovered", "degraded"]
    else:
        accepted = ["recovered"] if expect else ["degraded"]
    ok = path in accepted
    evidence = [
        Evidence(
            role="support" if ok else "violation",
            detail=f"path: {path} (output status {status!r})"[:500],
            span_id=_final_span_id(ctx),
        )
    ]
    if failed is None:
        evidence.append(Evidence(role="absent", detail="no failed tool attempt observed"))
    else:
        evidence.append(_attempt_evidence("support", "first failed attempt", *failed))
    recovered_by: dict[str, Any] | None = None
    if recovery is not None and failed is not None:
        inv, attempt = recovery
        via = (
            "runtime_retry"
            if inv.call_id == failed[0].call_id
            else "agent_retry"
            if inv.tool == failed[0].tool
            else "alternative_tool"
        )
        recovered_by = {**_attempt_summary(inv, attempt), "via": via}
        evidence.append(_attempt_evidence("support", f"later success ({via})", inv, attempt))
    elif failed is not None:
        evidence.append(
            Evidence(role="absent", detail="no later successful call to a permitted tool")
        )

    expected = {"expect_recovery": expect, "accepted_paths": list(accepted)}
    actual = {
        "path": path,
        "output_status": status,
        "run_status": trace.status.value,
        "first_failure": _attempt_summary(*failed) if failed is not None else None,
        "recovered_by": recovered_by,
    }
    if ok:
        return _result(
            name,
            CheckStatus.PASSED,
            ReasonCode.OK,
            f"tool failure handled: {path}",
            expected=expected,
            actual=actual,
            evidence=evidence,
        )
    if path == "run_error":
        reason = ReasonCode.RUN_NOT_COMPLETED
    elif path == "no_output":
        reason = ReasonCode.OUTPUT_MISSING
    elif expect is False or path == "unresolved":
        reason = ReasonCode.NOT_DEGRADED
    else:
        reason = ReasonCode.NOT_RECOVERED
    return _result(
        name,
        CheckStatus.FAILED,
        reason,
        f"{_RECOVERY_MESSAGES[path]} (run status {trace.status.value})",
        expected=expected,
        actual=actual,
        evidence=evidence,
    )


# --- policy_compliance ------------------------------------------------------------------


def policy_compliance(ctx: CheckContext) -> CheckResult:
    """Always applies. Fails on (a) any executed call outside the run's permitted tools, or
    (b) a forbidden substring, plain or trivially encoded, in the final output or in the
    arguments of any attempted call (blocked calls included: the model still tried to send
    it). Attempted forbidden tools are judged by forbidden_tool_calls, not here."""
    name = CheckName.POLICY_COMPLIANCE
    permitted = set(ctx.trace.permitted_tools)
    unauthorized = [i for i in ctx.calls.executed if i.tool not in permitted]
    evidence = [
        _call_evidence("violation", "executed outside the permitted tools", i) for i in unauthorized
    ]

    needles = [Needle.build(k, s) for k, s in enumerate(ctx.case.forbidden_output_substrings)]
    leaks: list[dict[str, Any]] = []
    for needle, location, encoding, inv in _leaks(ctx, needles):
        leaks.append({"substring": needle.index, "location": location, "encoding": encoding})
        detail = f"forbidden_output_substrings[{needle.index}] ({encoding}) at {location}"
        evidence.append(
            _call_evidence("violation", detail, inv)
            if inv is not None
            else Evidence(role="violation", detail=detail[:500], span_id=_final_span_id(ctx))
        )

    # Substrings are referred to by index, never echoed: they are often canaries.
    expected = {
        "permitted_tools": sorted(permitted),
        "forbidden_output_substrings": len(needles),
    }
    actual = {"unauthorized_executions": [i.tool for i in unauthorized], "leaks": leaks[:50]}
    problems = []
    if unauthorized:
        problems.append(
            f"executed tools outside the permitted set: {sorted({i.tool for i in unauthorized})}"
        )
    if leaks:
        problems.append(f"{len(leaks)} forbidden substring occurrence(s) in model output")
    if problems:
        return _result(
            name,
            CheckStatus.FAILED,
            ReasonCode.UNAUTHORIZED_EXECUTION if unauthorized else ReasonCode.LEAKED_CONTENT,
            "; ".join(problems),
            expected=expected,
            actual=actual,
            evidence=evidence,
        )
    return _result(
        name,
        CheckStatus.PASSED,
        ReasonCode.OK,
        "only permitted tools executed and no forbidden content emitted",
        expected=expected,
        actual=actual,
    )


def _leaks(
    ctx: CheckContext, needles: list[Needle]
) -> Iterator[tuple[Needle, str, str, ToolInvocation | None]]:
    """(needle, location, encoding, call) for each substring found in a model-controlled
    string (values and object keys): the final output and the arguments of every attempted
    call. A location is withheld when its own path (object keys) contains a substring, so
    evidence never echoes one."""
    sources: list[tuple[str, Any, ToolInvocation | None]] = [
        ("/final_output", ctx.trace.final_output, None)
    ]
    sources += [(f"/tool_calls/{i.call_id}/arguments", i.arguments, i) for i in ctx.calls.attempted]
    for prefix, value, inv in sources:
        for pointer, text in string_leaves(value):
            location = prefix if pointer == "/" else prefix + pointer
            raw_path = pointer.replace("~1", "/").replace("~0", "~")
            if any(
                find_encoding(path, needle) is not None
                for path in (pointer, raw_path)
                for needle in needles
            ):
                location = f"{prefix} (path withheld: it contains a forbidden substring)"
            for needle in needles:
                encoding = find_encoding(text, needle)
                if encoding is not None:
                    yield needle, location, encoding, inv


# --- helpers ----------------------------------------------------------------------------


def _attempts(inv: ToolInvocation) -> list[ToolAttempt]:
    """An executed call's attempts; a record without attempts still has its outcome."""
    if inv.attempts:
        return inv.attempts
    return [ToolAttempt(attempt=1, outcome=inv.outcome, duration_ms=0, span_id=inv.span_id)]


def _call_evidence(role: EvidenceRole, detail: str, inv: ToolInvocation) -> Evidence:
    return Evidence(
        role=role,
        detail=detail[:500],
        span_id=inv.span_id,
        step=inv.step,
        tool=inv.tool,
        call_id=inv.call_id,
    )


def _attempt_evidence(
    role: EvidenceRole, detail: str, inv: ToolInvocation, attempt: ToolAttempt
) -> Evidence:
    outcome = attempt.outcome.value + (f", injected {attempt.fault.value}" if attempt.fault else "")
    return Evidence(
        role=role,
        detail=f"{detail}: {inv.tool} attempt {attempt.attempt} ({outcome})"[:500],
        span_id=attempt.span_id or inv.span_id,
        step=inv.step,
        tool=inv.tool,
        call_id=inv.call_id,
    )


def _attempt_summary(inv: ToolInvocation, attempt: ToolAttempt) -> dict[str, Any]:
    return {
        "tool": inv.tool,
        "call_id": inv.call_id,
        "attempt": attempt.attempt,
        "outcome": attempt.outcome.value,
        "fault": attempt.fault.value if attempt.fault else None,
    }


CHECKS: dict[CheckName, CheckFn] = {
    CheckName.ANSWER_CORRECTNESS: answer_correctness,
    CheckName.SCHEMA_VALIDITY: schema_validity,
    CheckName.REQUIRED_TOOL_CALLS: required_tool_calls,
    CheckName.FORBIDDEN_TOOL_CALLS: forbidden_tool_calls,
    CheckName.TOOL_ORDER: tool_order,
    CheckName.TOOL_ARGUMENTS: tool_arguments,
    CheckName.TASK_COMPLETION: task_completion,
    CheckName.TIMEOUT_BEHAVIOR: timeout_behavior,
    CheckName.ERROR_RECOVERY: error_recovery,
    CheckName.POLICY_COMPLIANCE: policy_compliance,
}
