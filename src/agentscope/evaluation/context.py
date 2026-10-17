"""Inputs shared by all checks, derived once per (case, trace)."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from agentscope.domain.datasets import EvalCase
from agentscope.domain.tools import ToolOutcome
from agentscope.domain.traces import ToolInvocation, TraceRecord

# Output fields that qualify or explain a result instead of carrying one: a declining output
# may fill them (why it declined, what it consulted, which risks it saw) and still have no
# answer. Every other field of an agent's output is an answer field.
NON_ANSWER_FIELDS = frozenset({"status", "notes", "confidence", "citations", "risks", "goal"})


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
    # The output fields that carry the agent's answer (for the research assistant `answer`,
    # for the planner `steps` and `total_hours`). A declining output must leave them empty.
    answer_fields: tuple[str, ...]

    @classmethod
    def build(
        cls,
        case: EvalCase,
        trace: TraceRecord,
        output_schema: dict[str, Any],
        answer_fields: Iterable[str] | None = None,
    ) -> CheckContext:
        """`answer_fields` defaults to every property the output schema declares, except
        NON_ANSWER_FIELDS; a caller that knows the agent better can name them instead."""
        return cls(
            case=case,
            trace=trace,
            output_schema=output_schema,
            calls=CallSets.from_trace(trace),
            answer_fields=tuple(
                answer_fields if answer_fields is not None else schema_answer_fields(output_schema)
            ),
        )


def schema_answer_fields(output_schema: dict[str, Any]) -> tuple[str, ...]:
    properties = output_schema.get("properties")
    if not isinstance(properties, dict):
        return ()
    return tuple(name for name in properties if name not in NON_ANSWER_FIELDS)
