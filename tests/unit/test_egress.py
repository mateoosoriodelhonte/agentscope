"""The egress policy: only destinations declared in Settings, only fixed routes, no
redirects, no environment proxies, and no credentials over plain http to remote hosts."""

from __future__ import annotations

import dataclasses
from typing import Any

import httpx
import pytest

from agentscope.config import Settings
from agentscope.providers.http import (
    OLLAMA_ROUTES,
    EgressDenied,
    EgressPolicy,
    EgressTarget,
    egress_client,
)


def policy(**settings: Any) -> EgressPolicy:
    return EgressPolicy.from_settings(Settings(**settings))


class Recorder:
    """An httpx handler that records every request reaching the network layer."""

    def __init__(self, response: httpx.Response | None = None) -> None:
        self.requests: list[httpx.Request] = []
        self._response = response or httpx.Response(200, json={"ok": True})

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self._response


# --- the policy is derived from Settings only ----------------------------------------------


def test_default_policy_allows_only_local_ollama() -> None:
    p = policy()
    assert set(p.targets) == {"ollama"}
    assert not p.refusals
    ollama = p.targets["ollama"]
    assert (ollama.scheme, ollama.host, ollama.port, ollama.base_path) == (
        "http",
        "127.0.0.1",
        11434,
        "",
    )
    assert ollama.loopback
    assert ollama.routes == OLLAMA_ROUTES


def test_disabled_ollama_is_not_a_target() -> None:
    p = policy(enable_ollama=False)
    assert not p.targets
    with pytest.raises(EgressDenied, match="not an enabled egress target"):
        p.target("ollama")


def test_openai_compatible_url_is_a_target_only_when_enabled() -> None:
    url = "http://127.0.0.1:8080/v1"
    assert "openai_compatible" not in policy(openai_compatible_url=url).targets
    target = policy(openai_compatible_url=url, enable_openai_compatible=True).targets[
        "openai_compatible"
    ]
    assert (target.host, target.port, target.base_path) == ("127.0.0.1", 8080, "/v1")
    assert target.url("/chat/completions") == "http://127.0.0.1:8080/v1/chat/completions"


def test_enabled_openai_compatible_without_url_is_refused() -> None:
    p = policy(enable_openai_compatible=True)
    assert "AGENTSCOPE_OPENAI_COMPATIBLE_URL is not set" in p.refusals["openai_compatible"]
    with pytest.raises(EgressDenied, match="not set"):
        p.target("openai_compatible")


@pytest.mark.parametrize(
    ("endpoint", "base_path"),
    [("http://127.0.0.1:4318", ""), ("http://127.0.0.1:4318/v1/traces", "")],
)
def test_otlp_endpoint_is_a_target_when_set(endpoint: str, base_path: str) -> None:
    target = policy(otlp_endpoint=endpoint).targets["otlp"]
    assert target.base_path == base_path
    assert target.routes == frozenset({("POST", "/v1/traces")})


def test_non_loopback_hosts_require_explicit_allowlisting() -> None:
    refused = policy(ollama_url="http://gpu-box:11434", otlp_endpoint="http://collector:4318")
    assert not refused.targets
    assert "AGENTSCOPE_ALLOWED_EGRESS_HOSTS" in refused.refusals["ollama"]
    assert "leaves this machine" in refused.refusals["otlp"]

    allowed = policy(ollama_url="http://gpu-box:11434", allowed_egress_hosts=["GPU-Box"])
    assert not allowed.targets["ollama"].loopback
    # The earlier setting name is still honoured.
    legacy = policy(ollama_url="http://gpu-box:11434", allowed_model_hosts=["gpu-box"])
    assert "ollama" in legacy.targets


def test_allowlisting_one_host_does_not_admit_another() -> None:
    p = policy(ollama_url="http://gpu-box-2:11434", allowed_egress_hosts=["gpu-box"])
    assert "ollama" in p.refusals


@pytest.mark.parametrize(
    "url", ["http://[::1]:11434", "http://localhost:11434", "http://127.0.0.2"]
)
def test_loopback_forms_are_allowed_without_allowlisting(url: str) -> None:
    assert policy(ollama_url=url).targets["ollama"].loopback


def test_ipv6_addresses_are_bracketed_for_display() -> None:
    assert policy(ollama_url="http://[::1]:11434").targets["ollama"].address == "[::1]:11434"


def test_credentials_require_https_for_non_loopback_hosts() -> None:
    common: dict[str, Any] = {
        "enable_openai_compatible": True,
        "openai_compatible_api_key": "sk-test-0123456789abcdef",
        "allowed_egress_hosts": ["models.example.com"],
    }
    plain = policy(openai_compatible_url="http://models.example.com/v1", **common)
    assert "over plain http" in plain.refusals["openai_compatible"]
    secure = policy(openai_compatible_url="https://models.example.com/v1", **common)
    assert secure.targets["openai_compatible"].scheme == "https"
    # Without a key there is nothing to protect in transit.
    keyless = {**common, "openai_compatible_api_key": None}
    assert (
        "openai_compatible"
        in policy(openai_compatible_url="http://models.example.com/v1", **keyless).targets
    )
    # Loopback traffic never leaves the machine.
    assert (
        "openai_compatible"
        in policy(openai_compatible_url="http://127.0.0.1:8080/v1", **common).targets
    )


@pytest.mark.parametrize(
    ("url", "reason"),
    [
        ("http://user:hunter2-secret@127.0.0.1:11434", "must not embed credentials"),
        ("http://127.0.0.1:11434?token=abc", "query or fragment"),
        ("ftp://127.0.0.1:11434", "http:// or https://"),
        ("not a url", "http:// or https://"),
        ("http://exa mple.com:11434", "invalid host or port"),
        ("http://127.0.0.1:99999", "invalid host or port"),
        ("http://127.0.0.1:11434/a//b", "unsupported path"),
        ("http://127.0.0.1:11434/.hidden", "unsupported path"),
    ],
)
def test_malformed_urls_are_refused_without_echoing_them(url: str, reason: str) -> None:
    refusal = policy(ollama_url=url).refusals["ollama"]
    assert reason in refusal
    assert "hunter2" not in refusal
    assert "token=abc" not in refusal


def test_policy_is_immutable() -> None:
    p = policy()
    with pytest.raises(TypeError):
        p.targets["evil"] = p.targets["ollama"]  # type: ignore[index]
    with pytest.raises(dataclasses.FrozenInstanceError):
        p.targets["ollama"].host = "evil.example"  # type: ignore[misc]


# --- every request is checked by the transport ---------------------------------------------


@pytest.fixture
def ollama() -> EgressTarget:
    return policy().target("ollama")


async def test_allowed_route_reaches_the_network_layer(ollama: EgressTarget) -> None:
    network = Recorder()
    async with egress_client(ollama, timeout_s=5, transport=httpx.MockTransport(network)) as c:
        response = await c.get(ollama.url("/api/tags"))
    assert response.status_code == 200
    assert [str(r.url) for r in network.requests] == ["http://127.0.0.1:11434/api/tags"]


@pytest.mark.parametrize(
    ("method", "url"),
    [
        ("GET", "http://evil.example:11434/api/tags"),  # other host
        ("GET", "http://127.0.0.1:11435/api/tags"),  # other port
        ("GET", "https://127.0.0.1:11434/api/tags"),  # other scheme
        ("POST", "http://127.0.0.1:11434/api/pull"),  # other path: never download models
        ("DELETE", "http://127.0.0.1:11434/api/delete"),
        ("GET", "http://127.0.0.1:11434/api/tags?name=x"),  # no query strings
        ("GET", "http://127.0.0.1:11434/api/chat"),  # allowed path, wrong method
        ("POST", "http://127.0.0.1:8080/v1/chat/completions"),  # another provider's target
    ],
)
async def test_requests_outside_the_policy_never_reach_the_network(
    ollama: EgressTarget, method: str, url: str
) -> None:
    network = Recorder()
    async with egress_client(ollama, timeout_s=5, transport=httpx.MockTransport(network)) as c:
        with pytest.raises(EgressDenied, match="egress policy refused"):
            await c.request(method, url)
    assert network.requests == []


async def test_redirects_are_not_followed(ollama: EgressTarget) -> None:
    network = Recorder(httpx.Response(307, headers={"Location": "http://evil.example/steal"}))
    async with egress_client(ollama, timeout_s=5, transport=httpx.MockTransport(network)) as c:
        response = await c.get(ollama.url("/api/tags"))
        assert response.status_code == 307
        # Even a caller that opts into redirects cannot leave the policy.
        with pytest.raises(EgressDenied):
            await c.get(ollama.url("/api/tags"), follow_redirects=True)
    assert [r.url.host for r in network.requests] == ["127.0.0.1", "127.0.0.1"]


async def test_credentials_are_never_sent_to_a_remote_host_over_http() -> None:
    remote = policy(ollama_url="http://gpu-box:11434", allowed_egress_hosts=["gpu-box"]).target(
        "ollama"
    )
    network = Recorder()
    headers = {"Authorization": "Bearer abcdefghijklmnop"}
    async with egress_client(
        remote, timeout_s=5, headers=headers, transport=httpx.MockTransport(network)
    ) as c:
        with pytest.raises(EgressDenied, match="only over https"):
            await c.get(remote.url("/api/tags"))
    assert network.requests == []


async def test_credentials_may_go_to_loopback_over_http(ollama: EgressTarget) -> None:
    network = Recorder()
    async with egress_client(
        ollama,
        timeout_s=5,
        headers={"Authorization": "Bearer abcdefghijklmnop"},
        transport=httpx.MockTransport(network),
    ) as c:
        await c.get(ollama.url("/api/tags"))
    assert len(network.requests) == 1


async def test_environment_proxies_and_netrc_are_ignored(
    ollama: EgressTarget, monkeypatch: pytest.MonkeyPatch
) -> None:
    for var in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "all_proxy"):
        monkeypatch.setenv(var, "http://proxy.invalid:3128")
    async with egress_client(ollama, timeout_s=30) as client:
        assert client.trust_env is False
        assert client.follow_redirects is False
        # httpx routes requests through env proxies by mounting proxy transports; with
        # trust_env=False the policy transport handles every URL.
        transport = client._transport_for_url(httpx.URL(ollama.url("/api/tags")))
        assert type(transport).__name__ == "_PolicyTransport"
        assert not client._mounts


async def test_clients_have_explicit_timeouts(ollama: EgressTarget) -> None:
    async with egress_client(ollama, timeout_s=30) as client:
        assert client.timeout.read == 30
        assert client.timeout.connect == 5
    async with egress_client(ollama, timeout_s=2) as client:
        assert client.timeout.connect == 2
