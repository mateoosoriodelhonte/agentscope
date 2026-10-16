"""Provider status: what is enabled, and whether its configured target answers.

Only enabled providers are probed, only at targets the egress policy admits, and with a
short timeout. Targets are reported as host:port: configured URLs cannot carry credentials,
and API keys never appear in a status.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping

from agentscope.api.schemas import ProviderStatus
from agentscope.config import Settings
from agentscope.providers.base import ModelProvider, ProviderError
from agentscope.providers.fake import PROFILES
from agentscope.providers.http import EgressDenied, EgressPolicy
from agentscope.providers.ollama import OllamaProvider
from agentscope.providers.openai_compat import OpenAICompatProvider

PROBE_TIMEOUT_S = 2.0

_ENABLE_HINTS = {
    "ollama": "AGENTSCOPE_ENABLE_OLLAMA=true",
    "openai_compatible": "AGENTSCOPE_ENABLE_OPENAI_COMPATIBLE=true",
}


async def provider_statuses(
    settings: Settings, providers: Mapping[str, ModelProvider]
) -> list[ProviderStatus]:
    """One status per known provider, in a fixed order: fake, ollama, openai_compatible."""
    policy = EgressPolicy.from_settings(settings)
    fake_enabled = "fake" in providers
    fake = ProviderStatus(
        name="fake",
        enabled=fake_enabled,
        deterministic=True,
        reachable=True if fake_enabled else None,
        models=sorted(PROFILES),
        detail="Simulated model: demonstrates harness mechanics, not model behaviour.",
    )
    remote = await asyncio.gather(
        *(_probe(name, providers.get(name), policy) for name in _ENABLE_HINTS)
    )
    return [fake, *remote]


async def _probe(name: str, provider: ModelProvider | None, policy: EgressPolicy) -> ProviderStatus:
    if provider is None:
        return ProviderStatus(
            name=name,
            enabled=False,
            deterministic=False,
            detail=f"disabled; set {_ENABLE_HINTS[name]} to enable",
        )
    try:
        target = policy.target(name)
    except EgressDenied as exc:
        return ProviderStatus(name=name, enabled=True, deterministic=False, detail=str(exc))
    try:
        if isinstance(provider, OllamaProvider):
            version = await provider.version(PROBE_TIMEOUT_S)
            models = await provider.list_models(PROBE_TIMEOUT_S)
            detail = f"Ollama {version}; " + (
                f"{len(models)} model(s) installed"
                if models
                else "no models installed; download one yourself with 'ollama pull <model>'"
            )
        elif isinstance(provider, OpenAICompatProvider):
            models = await provider.list_models(PROBE_TIMEOUT_S)
            detail = f"{len(models)} model(s) offered"
        else:
            return ProviderStatus(
                name=name,
                enabled=True,
                deterministic=False,
                target=target.address,
                detail="enabled; this provider cannot be probed",
            )
    except ProviderError as exc:
        return ProviderStatus(
            name=name,
            enabled=True,
            deterministic=False,
            reachable=False,
            target=target.address,
            detail=str(exc),
        )
    return ProviderStatus(
        name=name,
        enabled=True,
        deterministic=False,
        reachable=True,
        target=target.address,
        models=models,
        detail=detail,
    )
