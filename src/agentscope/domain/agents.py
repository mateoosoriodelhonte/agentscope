"""Agent configuration: everything that distinguishes one agent version from another."""

from __future__ import annotations

from enum import StrEnum

from pydantic import Field, field_validator

from agentscope.domain.common import Identifier, Slug, StrictModel, content_hash
from agentscope.domain.tools import ToolOutcome


class ProviderName(StrEnum):
    FAKE = "fake"
    OLLAMA = "ollama"
    OPENAI_COMPATIBLE = "openai_compatible"


class GraphVariant(StrEnum):
    # plan -> guard -> act -> observe loop; the final output is accepted as produced.
    BASIC = "basic"
    # Adds an output validation node with a bounded repair loop.
    VALIDATED = "validated"


class ModelConfig(StrictModel):
    provider: ProviderName = ProviderName.FAKE
    # A model name only — never a URL. Where requests go is decided by Settings alone.
    model: str = Field(default="scripted-careful", pattern=r"^[A-Za-z0-9._:/-]{1,128}$")
    temperature: float = Field(default=0.0, ge=0.0, le=2.0)
    seed: int | None = Field(default=0)
    max_output_tokens: int = Field(default=1024, ge=16, le=4096)
    request_timeout_s: float = Field(default=60.0, gt=0, le=600)


# Outcomes a runtime retry can help with. Policy denials and invalid arguments never are.
RETRYABLE_OUTCOMES = frozenset({ToolOutcome.TIMEOUT, ToolOutcome.ERROR, ToolOutcome.MALFORMED})


class RetryPolicy(StrictModel):
    max_tool_retries: int = Field(default=0, ge=0, le=3)
    retry_on: list[ToolOutcome] = Field(
        default_factory=lambda: [ToolOutcome.TIMEOUT, ToolOutcome.ERROR], max_length=3
    )
    # Corrective re-prompts after a response that is not a valid decision.
    max_decision_repairs: int = Field(default=0, ge=0, le=3)
    # Corrective re-prompts after a final output that fails schema validation
    # (validated graph only).
    max_output_repairs: int = Field(default=0, ge=0, le=3)

    @field_validator("retry_on")
    @classmethod
    def _only_retryable(cls, value: list[ToolOutcome]) -> list[ToolOutcome]:
        refused = sorted(o.value for o in value if o not in RETRYABLE_OUTCOMES)
        if refused:
            allowed = sorted(o.value for o in RETRYABLE_OUTCOMES)
            raise ValueError(f"cannot retry on {refused}; retryable outcomes are {allowed}")
        return value


class Limits(StrictModel):
    max_steps: int = Field(default=8, ge=1, le=50)
    tool_timeout_s: float = Field(default=2.0, gt=0, le=30)
    run_timeout_s: float = Field(default=120.0, gt=0, le=600)
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
    tool_schema_version: int = Field(default=1, ge=1, le=100)
    graph: GraphVariant = GraphVariant.BASIC
    isolate_untrusted: bool = False
    retry: RetryPolicy = Field(default_factory=RetryPolicy)
    limits: Limits = Field(default_factory=Limits)

    @property
    def ref(self) -> str:
        return f"{self.agent}@{self.version}"

    def config_hash(self) -> str:
        """Hash of behaviour-relevant fields (label and description excluded).

        This identifies the configuration as written. `AgentCatalog.effective_config_hash`
        additionally covers what the references resolve to (prompt text, tool schemas,
        output schema, graph topology, fake-model brain version).
        """
        return content_hash(self.model_dump(mode="json", exclude={"label", "description"}))
