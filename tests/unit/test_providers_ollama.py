"""The Ollama provider against a mocked Ollama server (httpx.MockTransport, no sockets)."""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from typing import Any

import httpx
import pytest

from agentscope.agents.base import AgentCatalog
from agentscope.config import Settings
from agentscope.domain.agents import ModelConfig, ProviderName
from agentscope.domain.traces import RunStatus, TokenUsage
from agentscope.providers.base import ChatMessage, ModelRequest, ProviderError
from agentscope.providers.http import EgressDenied, EgressPolicy
from agentscope.providers.ollama import OllamaProvider
from agentscope.runtime.protocol import decision_schema
from agentscope.runtime.runner import AgentRunner, RunRequest
from agentscope.tools.registry import ToolRegistry

METADATA_CANARY = "METADATA-NEVER-SENT-5f1c"
Handler = Callable[[httpx.Request], httpx.Response]


class Server:
    """A scripted Ollama server that records every request it receives."""

    def __init__(self, handler: Handler) -> None:
        self.requests: list[httpx.Request] = []
        self._handler = handler

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self._handler(request)

    def body(self, index: int = 0) -> Any:
        return json.loads(self.requests[index].content)


def chat_reply(content: str = '{"type": "final"}', **extra: Any) -> Handler:
    payload = {
        "model": "llama3.2:latest",
        "message": {"role": "assistant", "content": content},
        "done": True,
        "done_reason": "stop",
        **extra,
    }
    return lambda request: httpx.Response(200, json=payload)


def provider(server: Server, **settings: Any) -> OllamaProvider:
    policy = EgressPolicy.from_settings(Settings(**settings))
    return OllamaProvider(policy, transport=httpx.MockTransport(server))


def model_request(catalog: AgentCatalog, **config: Any) -> ModelRequest:
    schema = decision_schema(catalog.get("research_assistant").output_schema())
    meta = {"kind": "observation", "output": METADATA_CANARY, "arguments": {"q": METADATA_CANARY}}
    return ModelRequest(
        agent="research_assistant",
        messages=[
            ChatMessage(role="system", content="You are a test."),
            ChatMessage(role="user", content="What year?", metadata={"kind": "task"}),
            ChatMessage(role="assistant", content='{"type": "tool_call"}'),
            ChatMessage(
                role="tool",
                name="search_documents",
                tool_call_id="call_001",
                content="Result of search_documents (call call_001): {}",
                metadata=meta,
            ),
        ],
        tools=[],
        response_schema=schema,
        config=ModelConfig.model_validate(
            {
                "provider": ProviderName.OLLAMA,
                "model": "llama3.2",
                "seed": 7,
                "temperature": 0.0,
                "max_output_tokens": 256,
                **config,
            }
        ),
    )


async def test_chat_request_shape(catalog: AgentCatalog) -> None:
    server = Server(chat_reply())
    request = model_request(catalog)
    await provider(server).generate(request)

    assert len(server.requests) == 1
    sent = server.requests[0]
    assert (sent.method, str(sent.url)) == ("POST", "http://127.0.0.1:11434/api/chat")
    assert server.body() == {
        "model": "llama3.2",
        "messages": [
            {"role": "system", "content": "You are a test."},
            {"role": "user", "content": "What year?"},
            {"role": "assistant", "content": '{"type": "tool_call"}'},
            # Observations travel as user turns (see providers/http.wire_messages).
            {"role": "user", "content": "Result of search_documents (call call_001): {}"},
        ],
        "stream": False,
        "format": request.response_schema,
        "options": {"temperature": 0.0, "seed": 7, "num_predict": 256},
        "think": False,
    }
    assert "authorization" not in sent.headers


async def test_message_metadata_is_never_sent(catalog: AgentCatalog) -> None:
    server = Server(chat_reply())
    await provider(server).generate(model_request(catalog))
    raw = server.requests[0].content.decode()
    assert METADATA_CANARY not in raw
    for key in ("metadata", "tool_call_id", 'call_001"', '"name"'):
        assert key not in raw


async def test_seed_is_omitted_when_unset(catalog: AgentCatalog) -> None:
    server = Server(chat_reply())
    await provider(server).generate(model_request(catalog, seed=None))
    assert "seed" not in server.body()["options"]


async def test_usage_is_reported_only_as_the_server_reports_it(catalog: AgentCatalog) -> None:
    server = Server(chat_reply(prompt_eval_count=120, eval_count=34))
    response = await provider(server).generate(model_request(catalog))
    assert response.usage == TokenUsage(
        input_tokens=120, output_tokens=34, reported_calls=1, total_calls=1
    )
    assert response.finish_reason == "stop"
    assert response.model == "llama3.2:latest"
    assert response.provider == "ollama"
    assert response.content == '{"type": "final"}'


async def test_missing_usage_is_none_never_zero(catalog: AgentCatalog) -> None:
    response = await provider(Server(chat_reply())).generate(model_request(catalog))
    assert response.usage is None


@pytest.mark.parametrize(
    ("counts", "expected"),
    [
        ({"eval_count": 9}, (None, 9)),  # prompt served from Ollama's cache
        ({"prompt_eval_count": "12", "eval_count": True}, None),  # not counts
        ({"prompt_eval_count": -1, "eval_count": 3}, (None, 3)),
    ],
)
async def test_partial_or_invalid_usage(
    catalog: AgentCatalog, counts: dict[str, Any], expected: tuple[int | None, int | None] | None
) -> None:
    response = await provider(Server(chat_reply(**counts))).generate(model_request(catalog))
    if expected is None:
        assert response.usage is None
    else:
        assert response.usage is not None
        assert (response.usage.input_tokens, response.usage.output_tokens) == expected


def raise_(exc_type: type[httpx.TransportError], message: str) -> Handler:
    def handler(request: httpx.Request) -> httpx.Response:
        raise exc_type(message, request=request)

    return handler


async def test_connection_refused(catalog: AgentCatalog) -> None:
    server = Server(raise_(httpx.ConnectError, "[Errno 61] Connection refused"))
    with pytest.raises(ProviderError, match=r"cannot connect to Ollama at 127\.0\.0\.1:11434"):
        await provider(server).generate(model_request(catalog))


async def test_timeout(catalog: AgentCatalog) -> None:
    server = Server(raise_(httpx.ReadTimeout, "timed out"))
    with pytest.raises(ProviderError, match="did not respond within 5s"):
        await provider(server).generate(model_request(catalog, request_timeout_s=5))


async def test_missing_model_tells_the_user_to_pull_it_and_never_pulls(
    catalog: AgentCatalog,
) -> None:
    server = Server(lambda r: httpx.Response(404, json={"error": "model 'llama3.2' not found"}))
    with pytest.raises(ProviderError) as caught:
        await provider(server).generate(model_request(catalog))
    assert "run 'ollama pull llama3.2' yourself" in str(caught.value)
    assert "never downloads" in str(caught.value)
    assert [r.url.path for r in server.requests] == ["/api/chat"]


@pytest.mark.parametrize(
    ("response", "message"),
    [
        (httpx.Response(200, content=b"<html>proxy login</html>"), "not a JSON object"),
        (httpx.Response(200, json=["not", "an", "object"]), "not a JSON object"),
        (httpx.Response(200, json={"done": True}), "no message content"),
        (httpx.Response(200, json={"message": {"content": 42}}), "no message content"),
        (httpx.Response(500, json={"error": "out of\nmemory"}), "HTTP 500: out of memory"),
        (httpx.Response(502, content=b"Bad Gateway"), r"HTTP 502$"),
        (httpx.Response(307, headers={"Location": "http://evil.example/"}), "redirect"),
    ],
)
async def test_bad_responses_become_provider_errors(
    catalog: AgentCatalog, response: httpx.Response, message: str
) -> None:
    server = Server(lambda r: response)
    with pytest.raises(ProviderError, match=message):
        await provider(server).generate(model_request(catalog))
    assert len(server.requests) == 1


async def test_refused_target_is_never_contacted(catalog: AgentCatalog) -> None:
    server = Server(chat_reply())
    remote = provider(server, ollama_url="http://gpu-box:11434")
    with pytest.raises(EgressDenied, match="not a loopback address"):
        await remote.generate(model_request(catalog))
    assert server.requests == []


async def test_list_models_and_version() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/version":
            return httpx.Response(200, json={"version": "0.12.6"})
        tags = [{"name": n} for n in ("qwen3:8b", "llama3.2:latest", "bad name; rm -rf /")]
        return httpx.Response(200, json={"models": [*tags, {"name": 3}, "junk"]})

    server = Server(handler)
    ollama = provider(server)
    assert await ollama.version(2.0) == "0.12.6"
    assert await ollama.list_models(2.0) == ["llama3.2:latest", "qwen3:8b"]
    assert [(r.method, r.url.path) for r in server.requests] == [
        ("GET", "/api/version"),
        ("GET", "/api/tags"),
    ]


async def test_list_models_rejects_a_malformed_listing() -> None:
    server = Server(lambda r: httpx.Response(200, json={"models": "everything"}))
    with pytest.raises(ProviderError, match="no model list"):
        await provider(server).list_models(2.0)


def test_remote_ollama_is_announced(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING, logger="agentscope.providers"):
        provider(
            Server(chat_reply()),
            ollama_url="http://gpu-box:11434",
            allowed_egress_hosts=["gpu-box"],
        )
    assert "gpu-box:11434" in caplog.text
    assert "leaves the machine" in caplog.text


def test_local_ollama_is_not_announced(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING, logger="agentscope.providers"):
        provider(Server(chat_reply()))
    assert caplog.text == ""


async def test_runner_end_to_end_through_the_ollama_provider(
    catalog: AgentCatalog, registry: ToolRegistry
) -> None:
    """The runtime's provider contract, with the HTTP layer mocked: a final decision from
    the server completes the run and provider-reported usage reaches the trace."""
    final = {
        "type": "final",
        "output": {"status": "insufficient_evidence", "confidence": "low"},
        "rationale": "nothing to look up",
    }
    server = Server(chat_reply(json.dumps(final), prompt_eval_count=50, eval_count=10))
    runner = AgentRunner(catalog, registry, {"ollama": provider(server)})
    config = catalog.resolve("research_assistant@v1").model_copy(
        update={"model": ModelConfig(provider=ProviderName.OLLAMA, model="llama3.2")}
    )
    trace = await runner.run(RunRequest(config=config, task="What year?"))

    assert trace.status is RunStatus.COMPLETED
    assert trace.provider == "ollama"
    assert trace.final_output == final["output"]
    assert trace.usage is not None
    assert (trace.usage.input_tokens, trace.usage.output_tokens) == (50, 10)
    assert server.body()["format"]["properties"]["type"]["enum"] == ["tool_call", "final"]
