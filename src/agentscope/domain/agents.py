"""Agent configuration: everything that distinguishes one agent version from another."""

from __future__ import annotations

from enum import StrEnum

from pydantic import Field

from agentscope.domain.common import Identifier, Slug, StrictModel, content_hash
from agentscope.domain.tools import ToolOutcome


class ProviderName(StrEnum):
    FAKE = "fake"
    OLLAMA = "ollama"
    OPENAI_COMPATIBLE = "openai_compatible"


class GraphVariant(StrEnum):
    # plan -> guard -> act -> observe loop; final output accepted as-is.
    BASIC = "basic"
    # Adds an output validation node with a bounded repair loop.
    VALIDATED = "validated"


class ModelConfig(StrictModel):
    provider: ProviderName = ProviderName.FAKE
    model: str = Field(default="scripted-careful", min_length=1, max_length=128)
    temperature: float = Field(default=0.0, ge=0.0, le=2.0)
    seed: int | None = Field(default=0)
    max_output_tokens: int | None = Field(default=None, ge=1, le=32768)
    request_timeout_s: float = Field(default=60.0, gt=0, le=600)


class RetryPolicy(StrictModel):
    max_tool_retries: int = Field(default=0, ge=0, le=5)
    retry_on: list[ToolOutcome] = Field(
        default_factory=lambda: [ToolOutcome.TIMEOUT, ToolOutcome.ERROR], max_length=8
    )
    max_output_repairs: int = Field(default=0, ge=0, le=3)


class Limits(StrictModel):
    max_steps: int = Field(default=8, ge=1, le=50)
    tool_timeout_s: float = Field(default=2.0, gt=0, le=60)
    run_timeout_s: float = Field(default=120.0, gt=0, le=1800)
    max_tool_output_chars: int = Field(default=20_000, ge=256, le=200_000)


class AgentConfig(StrictModel):
    """A fully specified, hashable agent version."""

    agent: Identifier
    version: Slug
    label: str = Field(default="", max_length=200)
    description: str = Field(default="", max_length=2000)
    prompt_version: Slug
    model: ModelConfig = Field(default_factory=ModelConfig)
    tools: list[Identifier] = Field(max_length=32)
    tool_schema_version: int = Field(default=1, ge=1)
    graph: GraphVariant = GraphVariant.BASIC
    isolate_untrusted: bool = False
    retry: RetryPolicy = Field(default_factory=RetryPolicy)
    limits: Limits = Field(default_factory=Limits)

    @property
    def ref(self) -> str:
        return f"{self.agent}@{self.version}"

    def config_hash(self) -> str:
        """Hash of behaviour-relevant fields (label/description excluded)."""
        return content_hash(self.model_dump(mode="json", exclude={"label", "description"}))
