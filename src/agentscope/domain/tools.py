"""Tool contracts: specifications, calls, results, and injected faults."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import Field, JsonValue

from agentscope.domain.common import Identifier, StrictModel


class ToolOutcome(StrEnum):
    OK = "ok"
    ERROR = "error"
    TIMEOUT = "timeout"
    MALFORMED = "malformed"
    PERMISSION_DENIED = "permission_denied"
    INVALID_ARGUMENTS = "invalid_arguments"
    UNKNOWN_TOOL = "unknown_tool"


class SideEffect(StrEnum):
    NONE = "none"
    # Mutates a per-run, in-memory sandbox only. Never touches the real world.
    SANDBOX_WRITE = "sandbox_write"


class FaultKind(StrEnum):
    TIMEOUT = "timeout"
    ERROR = "error"
    MALFORMED = "malformed"
    INCOMPLETE = "incomplete"
    MISLEADING = "misleading"
    INJECTION = "injection"
    PERMISSION_DENIED = "permission_denied"


# Faults whose purpose is to make a tool fail (as opposed to returning bad content).
FAILURE_FAULTS = frozenset(
    {FaultKind.TIMEOUT, FaultKind.ERROR, FaultKind.MALFORMED, FaultKind.PERMISSION_DENIED}
)


class ToolSpec(StrictModel):
    """What a model is told about a tool. `parameters` is a JSON Schema object."""

    name: Identifier
    version: int = Field(default=1, ge=1)
    description: str = Field(max_length=2000)
    parameters: dict[str, Any]
    side_effect: SideEffect = SideEffect.NONE


class FaultSpec(StrictModel):
    """A controlled failure applied to a tool during a run.

    `on_call` targets the n-th call (1-based, counting retries) of that tool; `None` means
    every call. For failure faults (timeout, error, permission_denied), `times` bounds how
    many consecutive calls fail from `on_call`. For output faults (malformed, incomplete,
    misleading, injection), `times` counts deliveries: the fault stays owed from `on_call`
    until it has actually rewritten that many outputs. `None` = no limit.
    """

    tool: Identifier
    kind: FaultKind
    on_call: int | None = Field(default=1, ge=1, le=50)
    times: int | None = Field(default=1, ge=1, le=50)
    payload: str | None = Field(default=None, min_length=1, max_length=8000)


class ModelFaultKind(StrEnum):
    INVALID_JSON = "invalid_json"  # response text is not JSON
    EMPTY = "empty"  # empty response
    SCHEMA_VIOLATION = "schema_violation"  # a final output missing required fields
    UNKNOWN_TOOL = "unknown_tool"  # requests a tool that does not exist


class ModelFaultSpec(StrictModel):
    """A controlled corruption of the model's n-th response (1-based)."""

    kind: ModelFaultKind
    on_call: int = Field(default=1, ge=1, le=50)
    times: int = Field(default=1, ge=1, le=50)


class ToolCall(StrictModel):
    call_id: str = Field(max_length=64)
    tool: str = Field(max_length=64)
    arguments: dict[str, JsonValue] = Field(default_factory=dict)


class ToolResult(StrictModel):
    """One attempt at executing a tool call."""

    call_id: str
    tool: str
    attempt: int = Field(default=1, ge=1)
    outcome: ToolOutcome
    output: JsonValue | None = None
    error: str | None = None
    duration_ms: float = Field(ge=0)
    fault: FaultKind | None = None
