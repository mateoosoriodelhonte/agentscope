"""Model provider interface.

Providers turn a `ModelRequest` (messages, tool specs, and a JSON Schema for the
response) into raw text plus provider-reported metadata. Parsing and validating the
structured decision happens in the runtime, uniformly for every provider.
"""

from __future__ import annotations

from typing import Any, Literal, Protocol, runtime_checkable

from pydantic import Field

from agentscope.domain.agents import ModelConfig
from agentscope.domain.common import StrictModel
from agentscope.domain.tools import ToolSpec
from agentscope.domain.traces import TokenUsage

Role = Literal["system", "user", "assistant", "tool"]


class ChatMessage(StrictModel):
    role: Role
    content: str
    name: str | None = None
    tool_call_id: str | None = None
    # Structured annotations for local test doubles and tracing. Never sent to a model.
    metadata: dict[str, Any] = Field(default_factory=dict, exclude=True)


class ModelRequest(StrictModel):
    agent: str
    messages: list[ChatMessage]
    tools: list[ToolSpec]
    response_schema: dict[str, Any]
    config: ModelConfig


class ModelResponse(StrictModel):
    content: str
    provider: str
    model: str
    usage: TokenUsage | None = None
    finish_reason: str | None = None
    # Set by the model-fault wrapper when this response was deliberately corrupted.
    injected_fault: str | None = None


class ProviderError(Exception):
    """The provider could not produce a response (network, model missing, policy)."""


@runtime_checkable
class ModelProvider(Protocol):
    name: str

    async def generate(self, request: ModelRequest) -> ModelResponse: ...
