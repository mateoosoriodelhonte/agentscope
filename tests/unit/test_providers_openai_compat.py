"""The opt-in OpenAI-compatible provider against a mocked server (httpx.MockTransport)."""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from typing import Any

import httpx
import pytest
from pydantic import SecretStr

from agentscope.agents.base import AgentCatalog
from agentscope.config import Settings
from agentscope.domain.agents import ModelConfig, ProviderName
from agentscope.domain.traces import TokenUsage
from agentscope.providers.base import ChatMessage, ModelRequest, ProviderError
from agentscope.providers.http import EgressDenied, EgressPolicy
from agentscope.providers.openai_compat import OpenAICompatProvider
from agentscope.runtime.protocol import decision_schema
from agentscope.services import build_redactor

API_KEY = "sk-test-DO-NOT-LEAK-0123456789abcdef"
METADATA_CANARY = "METADATA-NEVER-SENT-9a2e"
Handler = Callable[[httpx.Request], httpx.Response]


class Server:
    def __init__(self, handler: Handler) -> None:
        self.requests: list[httpx.Request] = []
        self._handler = handler

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self._handler(request)


def completion(content: Any = '{"type": "final"}', **extra: Any) -> Handler:
    payload = {
        "id": "chatcmpl-1",
        "model": "qwen3-8b-instruct",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }
        ],
        **extra,
    }
    return lambda request: httpx.Response(200, json=payload)


def settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "enable_openai_compatible": True,
        "openai_compatible_url": "http://127.0.0.1:8080/v1",
        "openai_compatible_api_key": API_KEY,
        **overrides,
    }
    return Settings(**values)


def provider(server: Server, **overrides: Any) -> OpenAICompatProvider:
    s = settings(**overrides)
    return OpenAICompatProvider(
        EgressPolicy.from_settings(s),
        s.openai_compatible_api_key,
        transport=httpx.MockTransport(server),
    )


def model_request(catalog: AgentCatalog, **config: Any) -> ModelRequest:
    return ModelRequest(
        agent="research_assistant",
        messages=[
            ChatMessage(role="system", content="You are a test."),
            ChatMessage(role="user", content="What year?"),
            ChatMessage(
                role="tool",
                name="search_documents",
                tool_call_id="call_001",
                content="Result of search_documents (call call_001): {}",
                metadata={"kind": "observation", "output": METADATA_CANARY},
            ),
        ],
        tools=[],
        response_schema=decision_schema(catalog.get("research_assistant").output_schema()),
        config=ModelConfig.model_validate(
            {
                "provider": ProviderName.OPENAI_COMPATIBLE,
                "model": "qwen3-8b-instruct",
                "seed": 3,
                "max_output_tokens": 512,
                **config,
            }
        ),
    )


async def test_chat_completions_request_shape(catalog: AgentCatalog) -> None:
    server = Server(completion())
    request = model_request(catalog)
    await provider(server).generate(request)

    sent = server.requests[0]
    assert (sent.method, str(sent.url)) == ("POST", "http://127.0.0.1:8080/v1/chat/completions")
    assert sent.headers["authorization"] == f"Bearer {API_KEY}"
    assert json.loads(sent.content) == {
        "model": "qwen3-8b-instruct",
        "messages": [
            {"role": "system", "content": "You are a test."},
            {"role": "user", "content": "What year?"},
            {"role": "user", "content": "Result of search_documents (call call_001): {}"},
        ],
        "temperature": 0.0,
        "seed": 3,
        "max_tokens": 512,
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "decision",
                "schema": request.response_schema,
                "strict": False,
            },
        },
    }
    assert METADATA_CANARY not in sent.content.decode()


async def test_no_authorization_header_without_a_key(catalog: AgentCatalog) -> None:
    server = Server(completion())
    await provider(server, openai_compatible_api_key=None).generate(model_request(catalog))
    assert "authorization" not in server.requests[0].headers


async def test_seed_is_omitted_when_unset(catalog: AgentCatalog) -> None:
    server = Server(completion())
    await provider(server).generate(model_request(catalog, seed=None))
    assert "seed" not in json.loads(server.requests[0].content)


async def test_usage_is_taken_from_the_server_report(catalog: AgentCatalog) -> None:
    server = Server(completion(usage={"prompt_tokens": 200, "completion_tokens": 40}))
    response = await provider(server).generate(model_request(catalog))
    assert response.usage == TokenUsage(
        input_tokens=200, output_tokens=40, reported_calls=1, total_calls=1
    )
    assert (response.model, response.finish_reason) == ("qwen3-8b-instruct", "stop")


@pytest.mark.parametrize("usage", [None, {}, {"total_tokens": 240}, "lots"])
async def test_missing_usage_is_none(catalog: AgentCatalog, usage: Any) -> None:
    extra = {} if usage is None else {"usage": usage}
    response = await provider(Server(completion(**extra))).generate(model_request(catalog))
    assert response.usage is None


@pytest.mark.parametrize(
    ("response", "message"),
    [
        (httpx.Response(200, content=b"not json"), "not a JSON object"),
        (httpx.Response(200, json={"choices": []}), "no message content"),
        (httpx.Response(200, json={"choices": ["x"]}), "no message content"),
        (
            httpx.Response(200, json={"choices": [{"message": {"content": None}}]}),
            "no message content",
        ),
        (httpx.Response(401, json={"error": {"message": "bad key"}}), "rejected the credentials"),
        (httpx.Response(429, json={"error": {"message": "slow down"}}), "HTTP 429: slow down"),
        (httpx.Response(302, headers={"Location": "https://evil.example/"}), "redirect"),
    ],
)
async def test_bad_responses_become_provider_errors(
    catalog: AgentCatalog, response: httpx.Response, message: str
) -> None:
    server = Server(lambda r: response)
    with pytest.raises(ProviderError, match=message):
        await provider(server).generate(model_request(catalog))
    assert len(server.requests) == 1


async def test_transport_failures(catalog: AgentCatalog) -> None:
    def refused(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("Connection refused", request=request)

    def slow(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out", request=request)

    with pytest.raises(ProviderError, match="cannot connect"):
        await provider(Server(refused)).generate(model_request(catalog))
    with pytest.raises(ProviderError, match="did not respond within 9s"):
        await provider(Server(slow)).generate(model_request(catalog, request_timeout_s=9))


@pytest.mark.parametrize("status", [400, 401, 500])
async def test_api_key_never_appears_in_errors_even_when_echoed(
    catalog: AgentCatalog, status: int
) -> None:
    echo = {"error": {"message": f"invalid header Authorization: Bearer {API_KEY}"}}
    server = Server(lambda r: httpx.Response(status, json=echo))
    with pytest.raises(ProviderError) as caught:
        await provider(server).generate(model_request(catalog))
    error = caught.value
    for text in (str(error), repr(error), repr(error.args)):
        assert API_KEY not in text
    # No unscrubbed exception is kept reachable through the chain.
    assert error.__cause__ is None
    assert error.__context__ is None


async def test_api_key_never_appears_in_transport_errors(catalog: AgentCatalog) -> None:
    def refused(request: httpx.Request) -> httpx.Response:
        # httpx masks Authorization in Headers reprs, so put the raw value in the message.
        raw = request.headers["authorization"]
        assert API_KEY in raw
        raise httpx.ConnectError(f"refused while sending {raw}", request=request)

    with pytest.raises(ProviderError) as caught:
        await provider(Server(refused)).generate(model_request(catalog))
    assert API_KEY not in str(caught.value)
    assert API_KEY not in repr(caught.value)
    # The httpx error holds the request, Authorization header included: it is not chained.
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None


def test_api_key_is_a_secret_everywhere_it_is_held(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.DEBUG):
        s = settings()
        p = provider(Server(completion()))
    assert isinstance(s.openai_compatible_api_key, SecretStr)
    for text in (repr(s), str(s), s.model_dump_json(), repr(p), repr(vars(p)), caplog.text):
        assert API_KEY not in text
    redacted = build_redactor(s).redact_text(f"leaked {API_KEY} here")
    assert API_KEY not in redacted


async def test_remote_base_url_requires_allowlisting(catalog: AgentCatalog) -> None:
    server = Server(completion())
    remote = provider(server, openai_compatible_url="https://models.example.com/v1")
    with pytest.raises(EgressDenied, match="AGENTSCOPE_ALLOWED_EGRESS_HOSTS"):
        await remote.generate(model_request(catalog))
    assert server.requests == []


async def test_remote_key_over_plain_http_is_refused(catalog: AgentCatalog) -> None:
    server = Server(completion())
    plain = provider(
        server,
        openai_compatible_url="http://models.example.com/v1",
        allowed_egress_hosts=["models.example.com"],
    )
    with pytest.raises(EgressDenied, match="over plain http"):
        await plain.generate(model_request(catalog))
    assert server.requests == []


async def test_allowlisted_remote_host_is_announced_and_used(
    catalog: AgentCatalog, caplog: pytest.LogCaptureFixture
) -> None:
    server = Server(completion())
    with caplog.at_level(logging.WARNING, logger="agentscope.providers"):
        remote = provider(
            server,
            openai_compatible_url="https://models.example.com/v1",
            allowed_egress_hosts=["models.example.com"],
        )
    assert "models.example.com:443" in caplog.text
    assert "leaves the machine" in caplog.text
    assert API_KEY not in caplog.text
    await remote.generate(model_request(catalog))
    assert str(server.requests[0].url) == "https://models.example.com/v1/chat/completions"


async def test_list_models() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert (request.method, request.url.path) == ("GET", "/v1/models")
        return httpx.Response(200, json={"data": [{"id": "b-model"}, {"id": "a-model"}, {}]})

    assert await provider(Server(handler)).list_models(2.0) == ["a-model", "b-model"]
