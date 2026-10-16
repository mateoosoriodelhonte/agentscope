"""Builds the set of enabled model providers from Settings."""

from __future__ import annotations

from agentscope.agents.base import AgentCatalog
from agentscope.config import Settings
from agentscope.providers.base import ModelProvider
from agentscope.providers.fake import FakeModelProvider
from agentscope.providers.http import EgressPolicy
from agentscope.providers.ollama import OllamaProvider
from agentscope.providers.openai_compat import OpenAICompatProvider


def build_providers(settings: Settings, catalog: AgentCatalog) -> dict[str, ModelProvider]:
    """The deterministic fake provider is always available.

    Ollama is enabled by default (`enable_ollama`) because it runs locally: being enabled
    only makes it selectable, and nothing is sent to `ollama_url` until a run selects
    provider=ollama (or the provider status is probed). The OpenAI-compatible provider sends
    data to a configurable server, so it exists only with `enable_openai_compatible`.
    Building a provider never opens a connection.
    """
    policy = EgressPolicy.from_settings(settings)
    providers: dict[str, ModelProvider] = {"fake": FakeModelProvider(catalog.brains())}
    if settings.enable_ollama:
        providers["ollama"] = OllamaProvider(policy)
    if settings.enable_openai_compatible:
        providers["openai_compatible"] = OpenAICompatProvider(
            policy, settings.openai_compatible_api_key
        )
    return providers
