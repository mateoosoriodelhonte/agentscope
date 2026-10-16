"""Trace contracts: one trace per agent run, built from OpenTelemetry spans."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import Field, JsonValue

from agentscope.domain.agents import AgentConfig, Limits
from agentscope.domain.common import StrictModel
from agentscope.domain.tools import FaultKind, FaultSpec, ModelFaultSpec, ToolOutcome


class SpanKind(StrEnum):
    RUN = "run"
    NODE = "node"
    LLM = "llm"
    TOOL = "tool"
    GUARDRAIL = "guardrail"


class SpanStatus(StrEnum):
    UNSET = "unset"
    OK = "ok"
    ERROR = "error"


class RunStatus(StrEnum):
    """How a run ended. A tool timeout is a tool outcome, never a run status."""

    RUNNING = "running"  # persisted while in flight; never the result of a finished run
    COMPLETED = "completed"  # a final answer was accepted
    FAILED = "failed"  # decision or output repair budget exhausted
    MAX_STEPS = "max_steps"  # step budget exhausted without a final answer
    TIMEOUT = "timeout"  # run deadline passed
    ERROR = "error"  # provider failure, harness defect, or interrupted run


ErrorCode = Literal[
    "provider_error",
    "provider_timeout",
    "invalid_decision",
    "invalid_output",
    "max_steps",
    "run_timeout",
    "internal_recursion_limit",
    "internal_error",
    "interrupted",
]


class SpanEvent(StrictModel):
    name: str
    timestamp: datetime
    attributes: dict[str, JsonValue] = Field(default_factory=dict)


class SpanRecord(StrictModel):
    trace_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    span_id: str = Field(pattern=r"^[0-9a-f]{16}$")
    parent_span_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{16}$")
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
    """A tool call requested by the model, and what the runtime did with it.

    `permitted=False` means the runtime blocked it before execution (not permitted, or
    unknown). `executed` is True only when the tool actually ran at least once.
    """

    call_id: str
    step: int = Field(ge=0)
    tool: str
    arguments: dict[str, JsonValue] = Field(default_factory=dict)
    permitted: bool
    executed: bool
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
    "decision_repaired",
    "output_invalid",
    "output_repaired",
    "max_steps",
    "model_fault_injected",
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
    # How many model calls reported usage, out of how many were made. When they differ,
    # the totals cover only the reporting calls.
    reported_calls: int = Field(default=0, ge=0)
    total_calls: int = Field(default=0, ge=0)
    source: Literal["provider_reported"] = "provider_reported"

    @property
    def complete(self) -> bool:
        return self.reported_calls == self.total_calls


class CostEstimate(StrictModel):
    """Cost derived only from explicitly configured per-model rates."""

    amount: float = Field(ge=0)
    currency: Literal["USD"] = "USD"
    input_per_1k_tokens: float = Field(ge=0)
    output_per_1k_tokens: float = Field(ge=0)
    partial: bool = False  # True when some model calls did not report usage
    source: Literal["estimate_from_configured_rates"] = "estimate_from_configured_rates"


class TraceRecord(StrictModel):
    trace_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    run_id: str
    agent: str
    agent_version: str
    config_hash: str
    effective_config_hash: str
    config: AgentConfig
    limits: Limits  # effective limits after case overrides (only ever tighter)
    permitted_tools: list[str]  # effective permitted set for this run
    task: str
    status: RunStatus
    error_code: ErrorCode | None = None
    error: str | None = None
    started_at: datetime
    ended_at: datetime
    duration_ms: float = Field(ge=0)
    steps: int = Field(ge=0)
    provider: str
    model: str
    final_output: JsonValue | None = None
    usage: TokenUsage | None = None
    cost: CostEstimate | None = None
    faults: list[FaultSpec] = Field(default_factory=list)
    model_faults: list[ModelFaultSpec] = Field(default_factory=list)
    case_id: str | None = None
    spans: list[SpanRecord] = Field(default_factory=list)
    transitions: list[StateTransition] = Field(default_factory=list)
    tool_invocations: list[ToolInvocation] = Field(default_factory=list)
    guardrail_events: list[GuardrailEvent] = Field(default_factory=list)


class RunSummary(StrictModel):
    """List-view header for a run (no spans or payloads)."""

    run_id: str
    trace_id: str
    agent: str
    agent_version: str
    config_hash: str
    provider: str
    model: str
    status: RunStatus
    error_code: str | None = None
    task: str
    case_id: str | None = None
    suite_id: str | None = None
    started_at: datetime
    duration_ms: float
    steps: int
    tool_calls: int
    blocked_tool_calls: int
    guardrail_events: int
    faults: int
    passed: bool | None = None  # verdict when the run was evaluated against a test case
