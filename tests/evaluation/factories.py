"""Hand-built traces for evaluator tests: no runtime, no model, every field explicit."""

from __future__ import annotations

import itertools
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from typing import Any

from agentscope.agents.research import RESEARCH_ASSISTANT
from agentscope.domain.agents import AgentConfig, RetryPolicy
from agentscope.domain.datasets import EvalCase
from agentscope.domain.evaluation import CaseResult, CheckName, CheckResult
from agentscope.domain.tools import FaultKind, FaultSpec, ToolOutcome
from agentscope.domain.traces import (
    RunStatus,
    SpanKind,
    SpanRecord,
    ToolAttempt,
    ToolInvocation,
    TraceRecord,
)
from agentscope.evaluation.checks import CHECKS
from agentscope.evaluation.context import CheckContext
from agentscope.evaluation.engine import evaluate_case

T0 = datetime(2026, 1, 1, tzinfo=UTC)
TRACE_ID = "0" * 31 + "1"
FINAL_SPAN = "f" * 16
TOOLS = ("search_documents", "retrieve_document")
SCHEMA = RESEARCH_ASSISTANT.output_schema()
_ids = itertools.count(1)


def span_id() -> str:
    return f"{next(_ids):016x}"


def answer(value: str | None = "1987", status: str = "answered", **extra: Any) -> dict[str, Any]:
    return {
        "status": status,
        "answer": value,
        "citations": [],
        "confidence": "medium",
        "notes": "",
        **extra,
    }


DECLINED = answer(None, "insufficient_evidence")


def attempt(
    outcome: ToolOutcome | str = ToolOutcome.OK, fault: FaultKind | str | None = None, n: int = 1
) -> ToolAttempt:
    return ToolAttempt(
        attempt=n,
        outcome=ToolOutcome(outcome),
        fault=FaultKind(fault) if fault else None,
        duration_ms=1.0,
        span_id=span_id(),
    )


def call(
    tool: str,
    args: dict[str, Any] | None = None,
    *,
    attempts: Sequence[ToolAttempt] | None = None,
    executed: bool = True,
    permitted: bool = True,
    outcome: ToolOutcome | str | None = None,
    step: int = 1,
    call_id: str | None = None,
) -> ToolInvocation:
    """An executed call succeeds by default; its outcome is its last attempt's outcome. A
    blocked call (`executed=False`) has no attempts."""
    if executed:
        attempts = list(attempts) if attempts is not None else [attempt()]
        final = ToolOutcome(outcome) if outcome else attempts[-1].outcome
    else:
        attempts = []
        final = ToolOutcome(outcome or ToolOutcome.PERMISSION_DENIED)
    return ToolInvocation(
        call_id=call_id or f"call_{next(_ids):03d}",
        step=step,
        tool=tool,
        arguments=args or {},
        permitted=permitted,
        executed=executed,
        denial_reason=None if executed else "not permitted",
        outcome=final,
        attempts=attempts,
        span_id=attempts[0].span_id if attempts else span_id(),
    )


def fault(tool: str, kind: FaultKind | str, **kw: Any) -> FaultSpec:
    return FaultSpec(tool=tool, kind=FaultKind(kind), **kw)


def trace(
    calls: Iterable[ToolInvocation] = (),
    *,
    output: Any = None,
    status: RunStatus = RunStatus.COMPLETED,
    permitted: Sequence[str] = TOOLS,
    retries: int = 0,
    steps: int | None = None,
    faults: Sequence[FaultSpec] = (),
) -> TraceRecord:
    calls = list(calls)
    config = AgentConfig(
        agent="research_assistant",
        version="v1",
        prompt_version="p1",
        tools=list(TOOLS),
        retry=RetryPolicy(max_tool_retries=retries),
    )
    final_span = SpanRecord(
        trace_id=TRACE_ID,
        span_id=FINAL_SPAN,
        name="chat scripted-naive",
        kind=SpanKind.LLM,
        start_time=T0,
        end_time=T0,
        duration_ms=0,
    )
    return TraceRecord(
        trace_id=TRACE_ID,
        run_id="run-1",
        agent=config.agent,
        agent_version=config.version,
        config_hash=config.config_hash(),
        effective_config_hash="sha256:test",
        config=config,
        limits=config.limits,
        permitted_tools=sorted(permitted),
        task="In what year did the Meridian Footbridge open?",
        status=status,
        started_at=T0,
        ended_at=T0,
        duration_ms=10.0,
        steps=steps if steps is not None else len(calls) + 1,
        provider="agentscope.fake",
        model="scripted-naive",
        final_output=output,
        faults=list(faults),
        spans=[final_span],
        tool_invocations=calls,
    )


def case(**fields: Any) -> EvalCase:
    fields.setdefault("id", "case-1")
    fields.setdefault("task", "In what year did the Meridian Footbridge open?")
    return EvalCase.model_validate(fields)


def run_check(name: CheckName, eval_case: EvalCase, record: TraceRecord) -> CheckResult:
    return CHECKS[name](CheckContext.build(eval_case, record, SCHEMA))


def evaluate(eval_case: EvalCase, record: TraceRecord) -> CaseResult:
    return evaluate_case(eval_case, record, SCHEMA)
