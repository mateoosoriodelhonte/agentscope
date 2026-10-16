"""Redaction of secrets from trace payloads.

Applied when data is recorded onto a span, i.e. before anything is persisted or exported.
Two mechanisms: values under sensitive key names are replaced wholesale, and secret-shaped
substrings are replaced anywhere in text.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

REDACTED = "[REDACTED]"

_SENSITIVE_KEY = re.compile(
    r"(?:^|[_.\-])(?:api[_\-]?key|apikey|authorization|auth[_\-]?token|password|passwd|passphrase"
    r"|secret|client[_\-]?secret|access[_\-]?token|refresh[_\-]?token|id[_\-]?token|bearer"
    r"|token|cookie|set[_\-]?cookie|private[_\-]?key|session[_\-]?id|credentials?)$",
    re.IGNORECASE,
)

_VALUE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "private_key",
        re.compile(
            r"-----BEGIN [A-Z ]{0,40}PRIVATE KEY-----.{0,8000}?"
            r"-----END [A-Z ]{0,40}PRIVATE KEY-----",
            re.S,
        ),
    ),
    ("anthropic_key", re.compile(r"\bsk-ant-[A-Za-z0-9_\-]{16,}")),
    ("openai_key", re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_\-]{16,}")),
    ("github_token", re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})")),
    ("aws_access_key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("slack_token", re.compile(r"\bxox[abprs]-[A-Za-z0-9\-]{10,}")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}")),
    ("bearer", re.compile(r"\b[Bb]earer\s+[A-Za-z0-9\-._~+/]{12,}=*")),
    (
        "assignment",
        re.compile(
            r"\b(?:password|passwd|secret|api[_\-]?key|access[_\-]?token)\s*[:=]\s*[\"']?[^\s\"',;]{4,}",
            re.IGNORECASE,
        ),
    ),
)

_MAX_DEPTH = 32


class Redactor:
    """Idempotent: redacting already-redacted data changes nothing."""

    def __init__(self, extra_keys: Iterable[str] = (), secret_values: Iterable[str] = ()) -> None:
        self._extra = frozenset(k.lower() for k in extra_keys)
        # Exact secret values known to the process (e.g. a configured API key). Short values
        # are ignored: replacing them would corrupt ordinary text.
        self._secrets = tuple(
            sorted({v for v in secret_values if len(v) >= 8}, key=len, reverse=True)
        )

    def is_sensitive_key(self, key: str) -> bool:
        return key.lower() in self._extra or bool(_SENSITIVE_KEY.search(key))

    def redact_text(self, text: str) -> str:
        for secret in self._secrets:
            if secret in text:
                text = text.replace(secret, "[REDACTED:configured_secret]")
        for kind, pattern in _VALUE_PATTERNS:
            text = pattern.sub(f"[REDACTED:{kind}]", text)
        return text

    def redact(self, value: Any, _depth: int = 0) -> Any:
        if _depth > _MAX_DEPTH:
            return "[TRUNCATED:depth]"
        if isinstance(value, str):
            return self.redact_text(value)
        if isinstance(value, dict):
            return {
                str(k): REDACTED if self.is_sensitive_key(str(k)) else self.redact(v, _depth + 1)
                for k, v in value.items()
            }
        if isinstance(value, list | tuple):
            return [self.redact(item, _depth + 1) for item in value]
        return value
