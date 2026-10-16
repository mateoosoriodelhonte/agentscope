"""Inputs shared by all checks, derived once per (case, trace)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agentscope.domain.datasets import EvalCase
from agentscope.domain.tools import ToolOutcome
from agentscope.domain.traces import ToolInvocation, TraceRecord


@dataclass(frozen=True)
class CallSets:
    """Three views of the run's tool calls.

    attempted: every tool call the model requested, including ones the runtime blocked.
    executed:  calls that passed the runtime guard and actually ran.
    succeeded: executed calls whose final outcome was `ok`.
    """

    attempted: tuple[ToolInvocation, ...]
    executed: tuple[ToolInvocation, ...]
    succeeded: tuple[ToolInvocation, ...]

    @classmethod
    def from_trace(cls, trace: TraceRecord) -> CallSets:
        attempted = tuple(trace.tool_invocations)
        executed = tuple(i for i in attempted if i.executed)
        succeeded = tuple(i for i in executed if i.outcome is ToolOutcome.OK)
        return cls(attempted, executed, succeeded)


@dataclass(frozen=True)
class CheckContext:
    case: EvalCase
    trace: TraceRecord
    # The agent's output JSON Schema, supplied by the caller. The evaluator never asks the
    # runtime whether the output was valid; it checks independently.
    output_schema: dict[str, Any]
    calls: CallSets

    @classmethod
    def build(
        cls, case: EvalCase, trace: TraceRecord, output_schema: dict[str, Any]
    ) -> CheckContext:
        return cls(
            case=case, trace=trace, output_schema=output_schema, calls=CallSets.from_trace(trace)
        )
