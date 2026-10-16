"""Which providers exist for given Settings, and what their status reports."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import httpx
import pytest

from agentscope.agents.base import AgentCatalog
from agentscope.api.schemas import ProviderStatus
from agentscope.config import Settings
from agentscope.providers.base import ModelProvider, ModelRequest, ModelResponse
from agentscope.providers.fake import FakeModelProvider
from agentscope.providers.http import EgressPolicy
from agentscope.providers.ollama import OllamaProvider
from agentscope.providers.openai_compat import OpenAICompatProvider
from agentscope.providers.registry import build_providers
from agentscope.providers.status import provider_statuses

API_KEY = "sk-test-STATUS-0123456789abcdef"
Handler = Callable[[httpx.Request], httpx.Response]


# --- registry ------------------------------------------------------------------------------


def test_defaults_enable_fake_and_local_ollama(catalog: AgentCatalog) -> None:
    providers = build_providers(Settings(), catalog)
    assert set(providers) == {"fake", "ollama"}
    assert isinstance(providers["fake"], FakeModelProvider)
    assert isinstance(providers["ollama"], OllamaProvider)


def test_fake_is_always_available(catalog: AgentCatalog) -> None:
    assert set(build_providers(Settings(enable_ollama=False), catalog)) == {"fake"}


def test_openai_compatible_is_opt_in(catalog: AgentCatalog) -> None:
    url = "http://127.0.0.1:8080/v1"
    assert "openai_compatible" not in build_providers(
        Settings(openai_compatible_url=url, openai_compatible_api_key=API_KEY), catalog
    )
    enabled = build_providers(
        Settings(enable_openai_compatible=True, openai_compatible_url=url), catalog
    )
    assert isinstance(enabled["openai_compatible"], OpenAICompatProvider)


def test_misconfigured_provider_is_registered_and_explains_itself(
    catalog: AgentCatalog, caplog: pytest.LogCaptureFixture
) -> None:
    providers = build_providers(Settings(ollama_url="http://gpu-box:11434"), catalog)
    assert "ollama" in providers  # selectable, so a run fails with the reason below
    assert "not a loopback address" in caplog.text


# --- status --------------------------------------------------------------------------------


class Server:
    def __init__(self, handler: Handler) -> None:
        self.requests: list[httpx.Request] = []
        self._handler = handler

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self._handler(request)


def ollama_server(models: list[str]) -> Server:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/version":
            return httpx.Response(200, json={"version": "0.12.6"})
        return httpx.Response(200, json={"models": [{"name": m} for m in models]})

    return Server(handler)


def refused(request: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError("Connection refused", request=request)


async def statuses(
    catalog: AgentCatalog,
    *,
    ollama: Server | None = None,
    openai: Server | None = None,
    **values: Any,
) -> dict[str, ProviderStatus]:
    settings = Settings(**values)
    policy = EgressPolicy.from_settings(settings)
    providers: dict[str, ModelProvider] = {"fake": FakeModelProvider(catalog.brains())}
    if ollama is not None:
        providers["ollama"] = OllamaProvider(policy, transport=httpx.MockTransport(ollama))
    if openai is not None:
        providers["openai_compatible"] = OpenAICompatProvider(
            policy, settings.openai_compatible_api_key, transport=httpx.MockTransport(openai)
        )
    result = await provider_statuses(settings, providers)
    assert [s.name for s in result] == ["fake", "ollama", "openai_compatible"]
    return {s.name: s for s in result}


async def test_status_of_the_default_setup(catalog: AgentCatalog) -> None:
    server = ollama_server(["qwen3:8b", "llama3.2:latest"])
    result = await statuses(catalog, ollama=server)

    fake = result["fake"]
    assert (fake.enabled, fake.deterministic, fake.reachable, fake.target) == (
        True,
        True,
        True,
        None,
    )
    assert "scripted-careful" in fake.models

    ollama = result["ollama"]
    assert (ollama.enabled, ollama.deterministic, ollama.reachable) == (True, False, True)
    assert ollama.target == "127.0.0.1:11434"
    assert ollama.models == ["llama3.2:latest", "qwen3:8b"]
    assert "Ollama 0.12.6" in ollama.detail
    # Probes use only read-only routes; nothing is pulled or generated.
    assert {(r.method, r.url.path) for r in server.requests} == {
        ("GET", "/api/version"),
        ("GET", "/api/tags"),
    }

    disabled = result["openai_compatible"]
    assert (disabled.enabled, disabled.reachable, disabled.target) == (False, None, None)
    assert "AGENTSCOPE_ENABLE_OPENAI_COMPATIBLE=true" in disabled.detail


async def test_ollama_without_models_suggests_pulling_one(catalog: AgentCatalog) -> None:
    result = await statuses(catalog, ollama=ollama_server([]))
    assert "ollama pull <model>" in result["ollama"].detail


async def test_unreachable_ollama(catalog: AgentCatalog) -> None:
    result = await statuses(catalog, ollama=Server(refused))
    ollama = result["ollama"]
    assert (ollama.reachable, ollama.target) == (False, "127.0.0.1:11434")
    assert "cannot connect" in ollama.detail


async def test_disabled_ollama_is_not_probed(catalog: AgentCatalog) -> None:
    result = await statuses(catalog, enable_ollama=False)
    assert (result["ollama"].enabled, result["ollama"].reachable) == (False, None)
    assert "AGENTSCOPE_ENABLE_OLLAMA=true" in result["ollama"].detail


async def test_refused_target_is_not_probed(catalog: AgentCatalog) -> None:
    server = ollama_server(["llama3.2"])
    result = await statuses(catalog, ollama=server, ollama_url="http://gpu-box:11434")
    assert (result["ollama"].reachable, result["ollama"].target) == (None, None)
    assert "AGENTSCOPE_ALLOWED_EGRESS_HOSTS" in result["ollama"].detail
    assert server.requests == []


async def test_openai_compatible_status_never_includes_the_key(catalog: AgentCatalog) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        echoed = f"unknown key Bearer {API_KEY}"
        return httpx.Response(500, json={"error": {"message": echoed}})

    result = await statuses(
        catalog,
        openai=Server(handler),
        enable_openai_compatible=True,
        openai_compatible_url="http://127.0.0.1:8080/v1",
        openai_compatible_api_key=API_KEY,
    )
    status = result["openai_compatible"]
    assert (status.enabled, status.reachable, status.target) == (True, False, "127.0.0.1:8080")
    assert API_KEY not in json.dumps([s.model_dump(mode="json") for s in result.values()])


async def test_openai_compatible_lists_models(catalog: AgentCatalog) -> None:
    server = Server(lambda r: httpx.Response(200, json={"data": [{"id": "m1"}]}))
    result = await statuses(
        catalog,
        openai=server,
        enable_openai_compatible=True,
        openai_compatible_url="http://127.0.0.1:8080/v1",
    )
    assert result["openai_compatible"].models == ["m1"]
    assert [r.url.path for r in server.requests] == ["/v1/models"]


async def test_providers_that_cannot_be_probed_are_reported_as_such(
    catalog: AgentCatalog,
) -> None:
    class Stub:
        name = "ollama"

        async def generate(self, request: ModelRequest) -> ModelResponse:
            raise NotImplementedError

    settings = Settings()
    result = await provider_statuses(settings, {"ollama": Stub()})
    by_name = {s.name: s for s in result}
    assert (by_name["fake"].enabled, by_name["fake"].reachable) == (False, None)
    assert (by_name["ollama"].reachable, by_name["ollama"].target) == (None, "127.0.0.1:11434")
    assert "cannot be probed" in by_name["ollama"].detail
