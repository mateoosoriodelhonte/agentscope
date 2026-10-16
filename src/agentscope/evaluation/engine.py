"""Evaluates one trace against one test case."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from agentscope.domain.datasets import EvalCase
from agentscope.domain.evaluation import CaseResult, CheckName, CheckStatus
from agentscope.domain.tools import ToolOutcome
from agentscope.domain.traces import TraceRecord
from agentscope.evaluation.checks import CHECKS
from agentscope.evaluation.context import CheckContext


def evaluate_case(
    case: EvalCase,
    trace: TraceRecord,
    output_schema: dict[str, Any],
    enabled: Iterable[CheckName] | None = None,
) -> CaseResult:
    ctx = CheckContext.build(case, trace, output_schema)
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
        invalid_output=schema_result is not None and schema_result.status is CheckStatus.FAILED,
        repaired_output=any(e.kind == "output_repaired" for e in trace.guardrail_events),
        unauthorized_tool_attempts=sum(1 for i in ctx.calls.attempted if i.tool not in permitted),
        unauthorized_tool_executions=sum(1 for i in ctx.calls.executed if i.tool not in permitted),
    )
