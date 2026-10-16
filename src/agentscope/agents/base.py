"""Agent definitions and the catalog of built-in agent versions."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel

from agentscope.domain.agents import AgentConfig, ProviderName
from agentscope.domain.common import content_hash
from agentscope.domain.tools import ToolSpec
from agentscope.providers.fake import FAKE_BRAIN_VERSION, Brain


@dataclass(frozen=True)
class AgentDefinition:
    """Static description of an agent: its tools, output contract, prompts, and versions."""

    name: str
    title: str
    description: str
    toolset: tuple[str, ...]
    output_model: type[BaseModel]
    prompts: dict[str, str]
    versions: tuple[AgentConfig, ...]
    brain: Brain

    def output_schema(self) -> dict[str, Any]:
        schema = self.output_model.model_json_schema()
        schema.pop("title", None)
        return schema

    def effective_config_hash(self, config: AgentConfig, tool_specs: list[ToolSpec]) -> str:
        """Hash of everything that determines behaviour, with references resolved: the
        configuration, the prompt text, the permitted tools' schemas, the output schema, the
        graph topology, and (for the fake provider) the scripted brain version."""
        from agentscope.runtime.graph import topology

        return content_hash(
            {
                "config": config.model_dump(mode="json", exclude={"label", "description"}),
                "prompt": self.prompt(config.prompt_version),
                "tools": [spec.model_dump(mode="json") for spec in tool_specs],
                "output_schema": self.output_schema(),
                "graph": topology(config.graph),
                "fake_brain_version": FAKE_BRAIN_VERSION
                if config.model.provider is ProviderName.FAKE
                else None,
            }
        )

    def prompt(self, version: str) -> str:
        try:
            return self.prompts[version]
        except KeyError:
            raise UnknownAgentError(
                f"agent {self.name!r} has no prompt version {version!r}; "
                f"available: {sorted(self.prompts)}"
            ) from None


class UnknownAgentError(LookupError):
    pass


class AgentCatalog:
    def __init__(self, definitions: Iterable[AgentDefinition]) -> None:
        self._definitions = {d.name: d for d in definitions}

    def names(self) -> list[str]:
        return sorted(self._definitions)

    def definitions(self) -> list[AgentDefinition]:
        return [self._definitions[n] for n in self.names()]

    def get(self, name: str) -> AgentDefinition:
        try:
            return self._definitions[name]
        except KeyError:
            raise UnknownAgentError(f"unknown agent {name!r}; available: {self.names()}") from None

    def brains(self) -> dict[str, Brain]:
        return {d.name: d.brain for d in self._definitions.values()}

    def resolve(self, ref: str) -> AgentConfig:
        """Resolve `agent@version` (or just `agent`, meaning its latest built-in version)."""
        name, _, version = ref.partition("@")
        definition = self.get(name)
        if not version:
            return definition.versions[-1]
        for config in definition.versions:
            if config.version == version:
                return config
        available = [c.version for c in definition.versions]
        raise UnknownAgentError(
            f"agent {name!r} has no version {version!r}; available: {available}"
        )

    def validate(self, config: AgentConfig) -> None:
        """Reject configurations that reference tools or prompts the agent does not have."""
        definition = self.get(config.agent)
        definition.prompt(config.prompt_version)
        unknown = sorted(set(config.tools) - set(definition.toolset))
        if unknown:
            raise UnknownAgentError(
                f"agent {config.agent!r} cannot use tools {unknown}; "
                f"toolset: {list(definition.toolset)}"
            )


def default_catalog() -> AgentCatalog:
    from agentscope.agents.planner import TASK_PLANNER
    from agentscope.agents.research import RESEARCH_ASSISTANT

    return AgentCatalog([RESEARCH_ASSISTANT, TASK_PLANNER])
