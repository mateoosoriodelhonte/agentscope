"""ASGI middleware that keeps the local API local.

- Host check: only loopback host names (defends against DNS rebinding).
- Remote mode: with an API token, every request (GETs and static files included) must carry
  `Authorization: Bearer <token>`, compared in constant time.
- Mutation guard: state-changing requests must be JSON, carry the X-AgentScope-Client
  header (which forces a CORS preflight that is never granted cross-origin), come from the
  server's own origin when an Origin header is present, and not be cross-site fetches.
- Body limit: counts received bytes regardless of what Content-Length claims.
- Security headers on every response (strict CSP, no sniffing, no referrer, no framing).
"""

from __future__ import annotations

import hmac
import json
from collections.abc import Iterable, Sequence
from typing import Any

from starlette.types import ASGIApp, Message, Receive, Scope, Send

LOOPBACK_HOSTNAMES = frozenset({"127.0.0.1", "localhost", "::1"})
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
CLIENT_HEADER = b"x-agentscope-client"

SECURITY_HEADERS: list[tuple[bytes, bytes]] = [
    (
        b"content-security-policy",
        b"default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
        b"font-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'none'; "
        b"form-action 'self'; frame-ancestors 'none'",
    ),
    (b"x-content-type-options", b"nosniff"),
    (b"referrer-policy", b"no-referrer"),
    (b"x-frame-options", b"DENY"),
    (b"cross-origin-opener-policy", b"same-origin"),
    (b"cross-origin-resource-policy", b"same-origin"),
]


def _headers(scope: Scope) -> dict[bytes, bytes]:
    return {k.lower(): v for k, v in scope.get("headers", [])}


def parse_hostname(host: str) -> str:
    """Hostname without port, IPv6-aware ('[::1]:8765' -> '::1')."""
    host = host.strip().lower()
    if host.startswith("["):
        end = host.find("]")
        return host[1:end] if end > 0 else host
    return host.rsplit(":", 1)[0] if host.count(":") == 1 else host


async def _reject(
    send: Send, status: int, detail: str, extra: Sequence[tuple[bytes, bytes]] = ()
) -> None:
    body = json.dumps({"detail": detail}).encode()
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [(b"content-type", b"application/json"), *extra, *SECURITY_HEADERS],
        }
    )
    await send({"type": "http.response.body", "body": body})


class LocalOnlyMiddleware:
    def __init__(
        self,
        app: ASGIApp,
        *,
        allowed_hosts: Iterable[str] = (),
        allowed_origins: Iterable[str] = (),
        max_body_bytes: int = 256 * 1024,
        large_body_paths: Iterable[str] = (),
        large_body_bytes: int = 10 * 1024 * 1024,
        api_token: str | None = None,
    ) -> None:
        self.app = app
        self.allowed_hosts = LOOPBACK_HOSTNAMES | {h.lower() for h in allowed_hosts if h != "*"}
        self.expected_authorization = f"Bearer {api_token}".encode() if api_token else None
        self.allowed_origins = {o.rstrip("/").lower() for o in allowed_origins}
        self.max_body_bytes = max_body_bytes
        self.large_body_paths = tuple(large_body_paths)
        self.large_body_bytes = large_body_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = _headers(scope)
        host = headers.get(b"host", b"").decode("latin-1")
        if parse_hostname(host) not in self.allowed_hosts:
            await _reject(send, 400, "invalid host header")
            return
        if self.expected_authorization is not None and not hmac.compare_digest(
            headers.get(b"authorization", b""), self.expected_authorization
        ):
            await _reject(
                send, 401, "missing or invalid API token", [(b"www-authenticate", b"Bearer")]
            )
            return

        method = scope["method"].upper()
        path: str = scope.get("path", "")
        if method not in SAFE_METHODS:
            problem = self._mutation_problem(headers, host, scope.get("scheme", "http"))
            if problem:
                await _reject(send, 403, problem)
                return

        limit = (
            self.large_body_bytes
            if any(path.startswith(p) for p in self.large_body_paths)
            else self.max_body_bytes
        )
        received = 0
        too_large = False

        async def limited_receive() -> Message:
            nonlocal received, too_large
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    too_large = True
                    return {"type": "http.disconnect"}
            return message

        response_started = False

        async def send_with_headers(message: Message) -> None:
            nonlocal response_started
            if message["type"] == "http.response.start":
                if too_large:
                    return
                response_started = True
                message["headers"] = [*message.get("headers", []), *SECURITY_HEADERS]
            elif too_large and not response_started:
                return
            await send(message)

        try:
            await self.app(scope, limited_receive, send_with_headers)
        except Exception:
            if not too_large:
                raise
        if too_large and not response_started:
            await _reject(send, 413, f"request body exceeds {limit} bytes")

    def _mutation_problem(self, headers: dict[bytes, bytes], host: str, scheme: str) -> str | None:
        content_type = headers.get(b"content-type", b"").split(b";")[0].strip().lower()
        if content_type != b"application/json":
            return "state-changing requests must use Content-Type: application/json"
        if headers.get(CLIENT_HEADER) != b"1":
            return "missing X-AgentScope-Client header"
        origin = headers.get(b"origin")
        if origin is not None:
            value = origin.decode("latin-1").rstrip("/").lower()
            own = f"{scheme}://{host}".lower()
            if value == "null" or (value != own and value not in self.allowed_origins):
                return "cross-origin request rejected"
        fetch_site = headers.get(b"sec-fetch-site")
        if fetch_site is not None and fetch_site.lower() not in (b"same-origin", b"none"):
            return "cross-site request rejected"
        return None


def security_header_dict() -> dict[str, Any]:
    return {k.decode(): v.decode() for k, v in SECURITY_HEADERS}
