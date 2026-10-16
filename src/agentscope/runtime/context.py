"""Per-run mutable context shared by graph nodes."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from agentscope.domain.agents import AgentConfig, Limits
from agentscope.domain.tools import ToolSpec
from agentscope.domain.traces import GuardrailEvent, GuardrailKind, TokenUsage, ToolInvocation
from agentscope.providers.base import ModelProvider
from agentscope.tools.executor import ToolExecutor
from agentscope.tracing.recorder import SpanHandle, TraceRecorder


@dataclass
class UsageAccumulator:
    """Sums provider-reported token usage. Calls that report nothing add nothing."""

    input_tokens: int = 0
    output_tokens: int = 0
    reported_calls: int = 0
    total_calls: int = 0

    def record(self, usage: TokenUsage | None) -> None:
        self.total_calls += 1
        if usage is None or usage.input_tokens is None or usage.output_tokens is None:
            return
        self.reported_calls += 1
        self.input_tokens += usage.input_tokens
        self.output_tokens += usage.output_tokens

    def result(self) -> TokenUsage | None:
        if self.reported_calls == 0:
            return None
        return TokenUsage(
            input_tokens=self.input_tokens,
            output_tokens=self.output_tokens,
            reported_calls=self.reported_calls,
            total_calls=self.total_calls,
        )


@dataclass
class RunContext:
    run_id: str
    config: AgentConfig
    limits: Limits
    permitted: frozenset[str]
    tool_specs: list[ToolSpec]
    output_schema: dict[str, Any]
    decision_schema: dict[str, Any]
    provider: ModelProvider
    provider_name: str
    executor: ToolExecutor
    recorder: TraceRecorder
    run_span: SpanHandle
    invocations: list[ToolInvocation] = field(default_factory=list)
    guardrails: list[GuardrailEvent] = field(default_factory=list)
    usage: UsageAccumulator = field(default_factory=UsageAccumulator)
    step: int = 0
    last_node: str = "START"
    awaiting_decision_repair: bool = False
    _calls: int = 0

    def next_call_id(self) -> str:
        self._calls += 1
        return f"call_{self._calls:03d}"

    def guardrail(
        self,
        kind: GuardrailKind,
        step: int,
        detail: str,
        *,
        tool: str | None = None,
        span_id: str | None = None,
    ) -> None:
        self.guardrails.append(
            GuardrailEvent(kind=kind, step=step, detail=detail, tool=tool, span_id=span_id)
        )
