"""Trace contracts: one trace per agent run, built from OpenTelemetry spans."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import Field, JsonValue

from agentscope.domain.agents import AgentConfig
from agentscope.domain.common import StrictModel
from agentscope.domain.tools import FaultKind, FaultSpec, ToolOutcome


class SpanKind(StrEnum):
    RUN = "run"
    NODE = "node"
    LLM = "llm"
    TOOL = "tool"
    GUARDRAIL = "guardrail"
    EVALUATION = "evaluation"


class SpanStatus(StrEnum):
    UNSET = "unset"
    OK = "ok"
    ERROR = "error"


class RunStatus(StrEnum):
    COMPLETED = "completed"
    FAILED = "failed"  # finished, but the final output was invalid or the model failed
    MAX_STEPS = "max_steps"
    TIMEOUT = "timeout"  # run-level timeout
    ERROR = "error"  # unexpected runtime error


class SpanEvent(StrictModel):
    name: str
    timestamp: datetime
    attributes: dict[str, JsonValue] = Field(default_factory=dict)


class SpanRecord(StrictModel):
    trace_id: str
    span_id: str
    parent_span_id: str | None = None
    name: str
    kind: SpanKind
    status: SpanStatus = SpanStatus.UNSET
    status_message: str | None = None
    start_time: datetime
    end_time: datetime
    duration_ms: float = Field(ge=0)
    attributes: dict[str, JsonValue] = Field(default_factory=dict)
    input: JsonValue | None = None
    output: JsonValue | None = None
    events: list[SpanEvent] = Field(default_factory=list)


class StateTransition(StrictModel):
    """An edge taken in the agent state machine."""

    seq: int = Field(ge=0)
    step: int = Field(ge=0)
    source: str
    target: str
    reason: str
    timestamp: datetime


class ToolAttempt(StrictModel):
    attempt: int = Field(ge=1)
    outcome: ToolOutcome
    fault: FaultKind | None = None
    duration_ms: float = Field(ge=0)
    error: str | None = None
    span_id: str | None = None


class ToolInvocation(StrictModel):
    """A tool call requested by the model, and what the runtime did with it."""

    call_id: str
    step: int = Field(ge=0)
    tool: str
    arguments: dict[str, JsonValue] = Field(default_factory=dict)
    permitted: bool
    denial_reason: str | None = None
    outcome: ToolOutcome
    attempts: list[ToolAttempt] = Field(default_factory=list)
    output: JsonValue | None = None
    span_id: str | None = None


GuardrailKind = Literal[
    "tool_denied",
    "unknown_tool",
    "invalid_arguments",
    "suspicious_content",
    "invalid_decision",
    "output_invalid",
    "output_repaired",
    "max_steps",
]


class GuardrailEvent(StrictModel):
    kind: GuardrailKind
    step: int = Field(ge=0)
    detail: str
    tool: str | None = None
    span_id: str | None = None


class TokenUsage(StrictModel):
    """Token counts exactly as reported by a provider. Never estimated."""

    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    source: Literal["provider"] = "provider"

    @property
    def total_tokens(self) -> int | None:
        if self.input_tokens is None or self.output_tokens is None:
            return None
        return self.input_tokens + self.output_tokens


class CostEstimate(StrictModel):
    """Cost derived only from explicitly configured per-model rates."""

    amount: float = Field(ge=0)
    currency: Literal["USD"] = "USD"
    input_per_1k_tokens: float = Field(ge=0)
    output_per_1k_tokens: float = Field(ge=0)
    source: Literal["configured_rate"] = "configured_rate"


class TraceRecord(StrictModel):
    trace_id: str
    run_id: str
    agent: str
    agent_version: str
    config_hash: str
    config: AgentConfig
    task: str
    status: RunStatus
    started_at: datetime
    ended_at: datetime
    duration_ms: float = Field(ge=0)
    steps: int = Field(ge=0)
    provider: str
    model: str
    final_output: JsonValue | None = None
    error: str | None = None
    usage: TokenUsage | None = None
    cost: CostEstimate | None = None
    faults: list[FaultSpec] = Field(default_factory=list)
    case_id: str | None = None
    spans: list[SpanRecord] = Field(default_factory=list)
    transitions: list[StateTransition] = Field(default_factory=list)
    tool_invocations: list[ToolInvocation] = Field(default_factory=list)
    guardrail_events: list[GuardrailEvent] = Field(default_factory=list)
