"""Local models through Ollama's native chat API.

Requests go only to the configured `ollama_url` (loopback by default), through the egress
policy. The reply is constrained with `format` set to the decision JSON Schema, sampling is
pinned (temperature, seed), output length is always capped (`num_predict`), and `think` is
off so reasoning models answer with the decision itself. Tool specs are not sent as native
tools: the system prompt lists them and the decision schema shapes the call.

AgentScope never downloads models. A missing model is reported together with the
`ollama pull` command for the user to run themselves.
"""

from __future__ import annotations

from typing import Any

import httpx

from agentscope.providers.base import ModelRequest, ModelResponse, ProviderError
from agentscope.providers.http import (
    EgressPolicy,
    announce,
    reported_usage,
    response_json,
    selectable_models,
    send,
    wire_messages,
)

LABEL = "Ollama"


class OllamaProvider:
    name = "ollama"

    def __init__(
        self, policy: EgressPolicy, *, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        self._policy = policy
        self._transport = transport
        announce(policy, self.name, LABEL)

    async def _call(
        self, method: str, path: str, timeout_s: float, payload: dict[str, Any] | None = None
    ) -> httpx.Response:
        return await send(
            self._policy.target(self.name),
            method,
            path,
            label=LABEL,
            timeout_s=timeout_s,
            payload=payload,
            transport=self._transport,
        )

    async def generate(self, request: ModelRequest) -> ModelResponse:
        config = request.config
        options: dict[str, Any] = {
            "temperature": config.temperature,
            "num_predict": config.max_output_tokens,
        }
        if config.seed is not None:
            options["seed"] = config.seed
        response = await self._call(
            "POST",
            "/api/chat",
            config.request_timeout_s,
            {
                "model": config.model,
                "messages": wire_messages(request.messages),
                "stream": False,
                "format": request.response_schema,
                "options": options,
                "think": False,
            },
        )
        if response.status_code == 404:
            raise ProviderError(
                f"{LABEL} has no model {config.model!r}. AgentScope never downloads models; "
                f"run 'ollama pull {config.model}' yourself, then retry."
            )
        body = response_json(response, LABEL)
        message = body.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, str):
            raise ProviderError(f"{LABEL} response has no message content")
        model = body.get("model")
        done_reason = body.get("done_reason")
        return ModelResponse(
            content=content,
            provider=self.name,
            model=model if isinstance(model, str) else config.model,
            # Ollama omits prompt_eval_count when the prompt was served from its cache.
            usage=reported_usage(body.get("prompt_eval_count"), body.get("eval_count")),
            finish_reason=done_reason if isinstance(done_reason, str) else None,
        )

    async def list_models(self, timeout_s: float) -> list[str]:
        """Locally installed models (GET /api/tags)."""
        body = response_json(await self._call("GET", "/api/tags", timeout_s), LABEL)
        models = body.get("models")
        if not isinstance(models, list):
            raise ProviderError(f"{LABEL} returned no model list")
        return selectable_models(m.get("name") for m in models if isinstance(m, dict))

    async def version(self, timeout_s: float) -> str:
        """The server's version (GET /api/version)."""
        body = response_json(await self._call("GET", "/api/version", timeout_s), LABEL)
        version = body.get("version")
        if not isinstance(version, str):
            raise ProviderError(f"{LABEL} returned no version")
        return version[:40]
