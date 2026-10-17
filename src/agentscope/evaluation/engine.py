"""Evaluates one trace against one test case."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from agentscope.domain.datasets import EvalCase
from agentscope.domain.evaluation import (
    CaseResult,
    CheckName,
    CheckResult,
    CheckStatus,
    ReasonCode,
)
from agentscope.domain.tools import ToolOutcome
from agentscope.domain.traces import RunStatus, TraceRecord
from agentscope.evaluation.checks import CHECKS
from agentscope.evaluation.context import CheckContext


def evaluate_case(
    case: EvalCase,
    trace: TraceRecord,
    output_schema: dict[str, Any],
    enabled: Iterable[CheckName] | None = None,
    *,
    answer_fields: Iterable[str] | None = None,
) -> CaseResult:
    """`answer_fields` names the output fields that carry the agent's answer; by default
    they are derived from the output schema (see CheckContext.build)."""
    ctx = CheckContext.build(case, trace, output_schema, answer_fields)
    names = list(CHECKS) if enabled is None else [n for n in CHECKS if n in set(enabled)]
    results = [CHECKS[name](ctx) for name in names]
    passed = all(r.status is not CheckStatus.FAILED for r in results)

    injected = 0
    unexpected = 0
    for inv in ctx.calls.executed:
        for attempt in inv.attempts:
            if attempt.outcome is ToolOutcome.OK:
                continue
            if attempt.fault is not None:
                injected += 1
            else:
                unexpected += 1
    permitted = set(trace.permitted_tools)
    schema_result = next((r for r in results if r.name is CheckName.SCHEMA_VALIDITY), None)
    return CaseResult(
        case_id=case.id,
        case_hash=case.case_hash(),
        run_id=trace.run_id,
        trace_id=trace.trace_id,
        passed=passed,
        status=trace.status,
        checks=results,
        steps=trace.steps,
        duration_ms=trace.duration_ms,
        tags=list(case.tags),
        injected_fault_outcomes=injected,
        unexpected_tool_errors=unexpected,
        invalid_output=_invalid_output(schema_result, trace),
        repaired_output=any(e.kind == "output_repaired" for e in trace.guardrail_events),
        # A request for a tool that does not exist (e.g. an injected unknown_tool model fault)
        # is not an attempt to use a forbidden capability, so it is not counted here.
        unauthorized_tool_attempts=sum(
            1
            for i in ctx.calls.attempted
            if i.tool not in permitted and i.outcome is not ToolOutcome.UNKNOWN_TOOL
        ),
        unauthorized_tool_executions=sum(1 for i in ctx.calls.executed if i.tool not in permitted),
    )


def _invalid_output(schema_result: CheckResult | None, trace: TraceRecord) -> bool:
    """The model produced an output that broke the schema, or never produced a parsable
    decision before its repair budget ran out. A run that simply ended without an output
    (timeout, max_steps, provider error) produced nothing invalid. Judged only while
    schema_validity is enabled; an exhausted decision repair budget counts even when the
    case accepts a run without output (must_complete=false), since the model's responses
    were invalid all the same."""
    if schema_result is None:
        return False
    if trace.status is RunStatus.FAILED and trace.error_code == "invalid_decision":
        return True
    return (
        schema_result.status is CheckStatus.FAILED
        and schema_result.reason is ReasonCode.SCHEMA_INVALID
    )
