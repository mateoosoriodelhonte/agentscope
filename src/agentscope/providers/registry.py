"""Builds the set of enabled model providers from Settings."""

from __future__ import annotations

from agentscope.agents.base import AgentCatalog
from agentscope.config import Settings
from agentscope.providers.base import ModelProvider
from agentscope.providers.fake import FakeModelProvider


def build_providers(settings: Settings, catalog: AgentCatalog) -> dict[str, ModelProvider]:
    """The deterministic fake provider is always available; real providers are opt-in."""
    return {"fake": FakeModelProvider(catalog.brains())}
