"""Outbound HTTP for model providers: an egress policy built from Settings alone, and the
only factory for HTTP clients, which enforces that policy on every request.

- Only destinations that Settings declare can be contacted: `ollama_url` (when Ollama is
  enabled), `openai_compatible_url` (when that provider is enabled) and `otlp_endpoint`
  (when set). Loopback hosts are allowed; any other host must be listed in
  `allowed_egress_hosts`, and credentials never go to a non-loopback host over plain http.
- A client may use only its own target's fixed routes (method and path).
- Clients never follow redirects (a redirect would carry headers to another origin) and
  ignore proxy, CA-bundle and netrc settings from the environment (`trust_env=False`), so
  nothing outside Settings can reroute traffic or attach credentials.
"""

from __future__ import annotations

import ipaddress
import json
import logging
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

import httpx

from agentscope.config import Settings
from agentscope.domain.traces import TokenUsage
from agentscope.providers.base import ChatMessage, ProviderError

logger = logging.getLogger("agentscope.providers")

# (HTTP method, path relative to the target's base path)
Route = tuple[str, str]

OLLAMA_ROUTES: frozenset[Route] = frozenset(
    {("POST", "/api/chat"), ("GET", "/api/tags"), ("GET", "/api/version")}
)
OPENAI_COMPATIBLE_ROUTES: frozenset[Route] = frozenset(
    {("POST", "/chat/completions"), ("GET", "/models")}
)
OTLP_ROUTES: frozenset[Route] = frozenset({("POST", "/v1/traces")})

CONNECT_TIMEOUT_S = 5.0

_DEFAULT_PORTS = {"http": 80, "https": 443}
# Lenient on underscores, which container DNS names often use.
_HOSTNAME = re.compile(r"[a-z0-9](?:[a-z0-9._-]{0,251}[a-z0-9])?")
_PATH_SEGMENT = re.compile(r"[A-Za-z0-9_~-][A-Za-z0-9._~-]{0,63}")
# Same pattern as ModelConfig.model: only names a run could actually select.
_MODEL_NAME = re.compile(r"[A-Za-z0-9._:/-]{1,128}")


class EgressDenied(ProviderError):
    """A destination or a request falls outside the egress policy."""


@dataclass(frozen=True)
class EgressTarget:
    """One destination Settings allow, and the only routes a client may use on it."""

    name: str
    scheme: str
    host: str  # lower-case ASCII (IDNA form); IPv6 without brackets
    port: int
    base_path: str  # "" or "/segment[/segment...]", no trailing slash
    routes: frozenset[Route]
    loopback: bool

    @property
    def address(self) -> str:
        """`host:port`, safe to show and log: configured URLs never carry credentials."""
        host = f"[{self.host}]" if ":" in self.host else self.host
        return f"{host}:{self.port}"

    def url(self, path: str) -> str:
        return f"{self.scheme}://{self.address}{self.base_path}{path}"

    def refusal(self, request: httpx.Request) -> str | None:
        """Why the policy refuses `request`, or None when it is allowed."""
        url = request.url
        host = url.raw_host.decode("ascii").lower()
        port = _port(url)
        if (url.scheme, host, port) != (self.scheme, self.host, self.port):
            return f"{url.scheme}://{host}:{port} is not the configured {self.name} target"
        path = url.raw_path.decode("ascii")  # includes the query string, so none is allowed
        if (request.method, path) not in {(m, self.base_path + p) for m, p in self.routes}:
            return f"{request.method} {path[:100]} is not an allowed {self.name} route"
        if "authorization" in request.headers and self.scheme != "https" and not self.loopback:
            return "credentials are sent to non-loopback hosts only over https"
        return None


@dataclass(frozen=True)
class EgressPolicy:
    """Every destination AgentScope may contact, derived from Settings and nothing else."""

    targets: Mapping[str, EgressTarget]
    # Destinations that are configured but refused, with a reason that is safe to show.
    refusals: Mapping[str, str]

    @classmethod
    def from_settings(cls, settings: Settings) -> EgressPolicy:
        allowlist = frozenset(
            host.strip().strip("[]").lower()
            for host in (*settings.allowed_egress_hosts, *settings.allowed_model_hosts)
        )
        targets: dict[str, EgressTarget] = {}
        refusals: dict[str, str] = {}

        def declare(
            name: str,
            setting: str,
            raw: str | None,
            routes: frozenset[Route],
            *,
            credentials: bool = False,
        ) -> None:
            try:
                targets[name] = _target(name, setting, raw, routes, allowlist, credentials)
            except EgressDenied as exc:
                refusals[name] = str(exc)

        if settings.enable_ollama:
            declare("ollama", "AGENTSCOPE_OLLAMA_URL", settings.ollama_url, OLLAMA_ROUTES)
        if settings.enable_openai_compatible:
            api_key = settings.openai_compatible_api_key
            declare(
                "openai_compatible",
                "AGENTSCOPE_OPENAI_COMPATIBLE_URL",
                settings.openai_compatible_url,
                OPENAI_COMPATIBLE_ROUTES,
                credentials=bool(api_key and api_key.get_secret_value()),
            )
        if settings.otlp_endpoint:
            # Accept either the collector's base URL or its full traces URL.
            base = settings.otlp_endpoint.rstrip("/").removesuffix("/v1/traces")
            declare("otlp", "AGENTSCOPE_OTLP_ENDPOINT", base, OTLP_ROUTES)
        return cls(MappingProxyType(targets), MappingProxyType(refusals))

    def target(self, name: str) -> EgressTarget:
        try:
            return self.targets[name]
        except KeyError:
            reason = self.refusals.get(name, f"{name} is not an enabled egress target")
            raise EgressDenied(reason) from None


def _port(url: httpx.URL) -> int | None:
    # httpx reports the scheme's default port as None. An explicit port 0 must stay 0 (and be
    # refused), not silently become the default port.
    return url.port if url.port is not None else _DEFAULT_PORTS.get(url.scheme)


def _ip(host: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    try:
        return ipaddress.ip_address(host)
    except ValueError:
        return None


def _target(
    name: str,
    setting: str,
    raw: str | None,
    routes: frozenset[Route],
    allowlist: frozenset[str],
    credentials: bool,
) -> EgressTarget:
    # Refusal messages never echo the configured URL: a malformed one may embed a secret.
    if not raw:
        raise EgressDenied(f"{name}: {setting} is not set")
    try:
        url = httpx.URL(raw)
    except httpx.InvalidURL:
        raise EgressDenied(f"{name}: {setting} is not a valid URL") from None
    if url.scheme not in _DEFAULT_PORTS:
        raise EgressDenied(f"{name}: {setting} must be an http:// or https:// URL")
    if url.userinfo:
        raise EgressDenied(f"{name}: {setting} must not embed credentials")
    if url.query or url.fragment:
        raise EgressDenied(f"{name}: {setting} must not have a query or fragment")
    host = url.raw_host.decode("ascii").lower()
    ip = _ip(host)
    port = _port(url)
    if not (ip or _HOSTNAME.fullmatch(host)) or port is None or not 0 < port < 65536:
        raise EgressDenied(f"{name}: {setting} has an invalid host or port")
    base_path = url.raw_path.decode("ascii").rstrip("/")
    if not all(_PATH_SEGMENT.fullmatch(segment) for segment in base_path.split("/")[1:]):
        raise EgressDenied(f"{name}: {setting} has an unsupported path")
    loopback = host == "localhost" or bool(ip and ip.is_loopback)
    target = EgressTarget(name, url.scheme, host, port, base_path, routes, loopback)
    if not loopback and host not in allowlist:
        raise EgressDenied(
            f"{name}: {target.address} is not a loopback address; add {host!r} to "
            "AGENTSCOPE_ALLOWED_EGRESS_HOSTS to allow it (data sent there leaves this machine)"
        )
    if credentials and not loopback and url.scheme != "https":
        raise EgressDenied(
            f"{name}: refusing to send credentials to {target.address} over plain http; use https"
        )
    return target


def announce(policy: EgressPolicy, name: str, label: str) -> None:
    """Say once, when a provider is built, what enabling it means for egress."""
    if name in policy.refusals:
        logger.warning("%s is enabled but cannot be used: %s", label, policy.refusals[name])
    target = policy.targets.get(name)
    if target is not None and not target.loopback:
        logger.warning(
            "%s will send prompts, tool outputs and task data to %s: this data leaves the machine.",
            label,
            target.address,
        )


class _PolicyTransport(httpx.AsyncBaseTransport):
    """Checks every request against one target before it reaches the network layer."""

    def __init__(self, target: EgressTarget, network: httpx.AsyncBaseTransport) -> None:
        self._target = target
        self._network = network

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        reason = self._target.refusal(request)
        if reason is not None:
            raise EgressDenied(f"egress policy refused a request: {reason}")
        return await self._network.handle_async_request(request)

    async def aclose(self) -> None:
        await self._network.aclose()


def egress_client(
    target: EgressTarget,
    *,
    timeout_s: float,
    headers: Mapping[str, str] | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> httpx.AsyncClient:
    """The only way AgentScope builds an outbound HTTP client.

    `transport` replaces the network layer underneath the policy check; tests pass an
    `httpx.MockTransport`, so the policy is exercised exactly as in production.
    """
    network = transport or httpx.AsyncHTTPTransport(trust_env=False, retries=0)
    return httpx.AsyncClient(
        transport=_PolicyTransport(target, network),
        headers=headers,
        timeout=httpx.Timeout(timeout_s, connect=min(timeout_s, CONNECT_TIMEOUT_S)),
        follow_redirects=False,
        trust_env=False,
    )


async def send(
    target: EgressTarget,
    method: str,
    path: str,
    *,
    label: str,
    timeout_s: float,
    payload: Mapping[str, Any] | None = None,
    headers: Mapping[str, str] | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> httpx.Response:
    """One request through the policy. Transport failures and redirects become
    ProviderErrors whose messages name the target but never headers or bodies."""
    failure: str | None = None
    async with egress_client(
        target, timeout_s=timeout_s, headers=headers, transport=transport
    ) as client:
        try:
            response = await client.request(method, target.url(path), json=payload)
        except httpx.TimeoutException:
            failure = f"{label} at {target.address} did not respond within {timeout_s:g}s"
        except httpx.ConnectError:
            failure = f"cannot connect to {label} at {target.address}; is it running?"
        except httpx.HTTPError as exc:
            # Every other httpx failure, including those that are not transport errors (for
            # example DecodingError on a body whose Content-Encoding is wrong).
            failure = f"request to {label} at {target.address} failed ({type(exc).__name__})"
    if failure is not None:
        # Raised outside the handler so the httpx error, which holds the request and its
        # headers, is not kept as __context__.
        raise ProviderError(failure)
    if 300 <= response.status_code < 400:
        raise ProviderError(
            f"{label} answered with a redirect (HTTP {response.status_code}); "
            "AgentScope never follows redirects"
        )
    return response


def _json(response: httpx.Response) -> Any:
    try:
        return json.loads(response.content)
    except (ValueError, RecursionError):  # undecodable bytes, bad JSON, absurd nesting
        return None


def printable(text: str, limit: int) -> str:
    """Server-supplied text made safe to show: whitespace runs collapsed, control and
    format characters (terminal escapes, bidi overrides) dropped, length capped."""
    return "".join(c for c in " ".join(text.split()) if c.isprintable())[:limit]


def response_json(response: httpx.Response, label: str) -> dict[str, Any]:
    """The JSON object of a successful response. An error status becomes a ProviderError
    carrying the server's own message (Ollama or OpenAI shape), shortened."""
    body = _json(response)
    if response.status_code >= 400:
        error = body.get("error") if isinstance(body, dict) else None
        if isinstance(error, dict):
            error = error.get("message")
        detail = ": " + printable(error, 200) if isinstance(error, str) else ""
        raise ProviderError(f"{label} returned HTTP {response.status_code}{detail}")
    if not isinstance(body, dict):
        raise ProviderError(f"{label} returned a body that is not a JSON object")
    return body


def _token_count(value: object) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    return None


def reported_usage(input_tokens: object, output_tokens: object) -> TokenUsage | None:
    """Token counts exactly as the server reported them; None when it reported neither.
    Anything that is not a non-negative integer counts as not reported."""
    prompt, completion = _token_count(input_tokens), _token_count(output_tokens)
    if prompt is None and completion is None:
        return None
    return TokenUsage(
        input_tokens=prompt, output_tokens=completion, reported_calls=1, total_calls=1
    )


def selectable_models(names: Iterable[object]) -> list[str]:
    """Server-reported model names that a run could select, sorted and de-duplicated."""
    return sorted({n for n in names if isinstance(n, str) and _MODEL_NAME.fullmatch(n)})


def wire_messages(messages: Sequence[ChatMessage]) -> list[dict[str, str]]:
    """Role and content only: `metadata` (runtime annotations for local test doubles),
    `name` and `tool_call_id` never leave the process.

    Decisions travel as JSON text rather than native tool calls, so tool observations are
    sent as user turns: OpenAI-compatible servers reject `tool` turns that do not answer a
    native tool call, and many Ollama chat templates silently drop them. The observation
    text itself names the tool and call id, and marks the content as untrusted when the
    agent isolates tool output.
    """
    return [
        {"role": "user" if m.role == "tool" else m.role, "content": m.content} for m in messages
    ]
