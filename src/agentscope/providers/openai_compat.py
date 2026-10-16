"""Opt-in adapter for servers that implement the OpenAI Chat Completions API (vLLM,
llama.cpp server, LM Studio, LiteLLM, hosted APIs), spoken directly over httpx.

Disabled unless `enable_openai_compatible`. Requests go only to `openai_compatible_url`
through the egress policy: a non-loopback base URL must be allowlisted explicitly (and is
announced with a warning, because data then leaves the machine), and a key is never sent to
a non-loopback host over plain http. The reply is constrained with a `json_schema` response
format in non-strict mode: strict mode requires every property to be required, and the
decision schema's tool, arguments and output fields are optional by design.

The API key stays a SecretStr until the Authorization header is built. It is registered
with the Redactor (services.build_redactor) and scrubbed from every error this provider
raises, even when a server echoes it back.
"""

from __future__ import annotations

from typing import Any

import httpx
from pydantic import SecretStr

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

LABEL = "OpenAI-compatible server"


class OpenAICompatProvider:
    name = "openai_compatible"

    def __init__(
        self,
        policy: EgressPolicy,
        api_key: SecretStr | None = None,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._policy = policy
        self._api_key = api_key
        self._transport = transport
        announce(policy, self.name, LABEL)

    async def _call(
        self, method: str, path: str, timeout_s: float, payload: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        key = self._api_key.get_secret_value() if self._api_key else ""
        try:
            response = await send(
                self._policy.target(self.name),
                method,
                path,
                label=LABEL,
                timeout_s=timeout_s,
                payload=payload,
                headers={"Authorization": f"Bearer {key}"} if key else None,
                transport=self._transport,
            )
            if response.status_code in (401, 403):
                raise ProviderError(
                    f"{LABEL} rejected the credentials (HTTP {response.status_code}); "
                    "check AGENTSCOPE_OPENAI_COMPATIBLE_API_KEY"
                )
            return response_json(response, LABEL)
        except ProviderError as exc:
            if not key or key not in str(exc):
                raise
            message = str(exc).replace(key, "[REDACTED]")
        # Raised outside the handler so the unscrubbed error is not kept as __context__.
        raise ProviderError(message)

    async def generate(self, request: ModelRequest) -> ModelResponse:
        config = request.config
        payload: dict[str, Any] = {
            "model": config.model,
            "messages": wire_messages(request.messages),
            "temperature": config.temperature,
            "max_tokens": config.max_output_tokens,
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "decision",
                    "schema": request.response_schema,
                    "strict": False,
                },
            },
        }
        if config.seed is not None:
            payload["seed"] = config.seed
        body = await self._call("POST", "/chat/completions", config.request_timeout_s, payload)
        choices = body.get("choices")
        first = choices[0] if isinstance(choices, list) and choices else None
        choice: dict[str, Any] = first if isinstance(first, dict) else {}
        message = choice.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, str):
            raise ProviderError(f"{LABEL} response has no message content")
        usage = body.get("usage")
        reported = usage if isinstance(usage, dict) else {}
        model = body.get("model")
        finish_reason = choice.get("finish_reason")
        return ModelResponse(
            content=content,
            provider=self.name,
            model=model if isinstance(model, str) else config.model,
            usage=reported_usage(reported.get("prompt_tokens"), reported.get("completion_tokens")),
            finish_reason=finish_reason if isinstance(finish_reason, str) else None,
        )

    async def list_models(self, timeout_s: float) -> list[str]:
        """Models the server offers (GET {base}/models)."""
        data = (await self._call("GET", "/models", timeout_s)).get("data")
        if not isinstance(data, list):
            raise ProviderError(f"{LABEL} returned no model list")
        return selectable_models(m.get("id") for m in data if isinstance(m, dict))
