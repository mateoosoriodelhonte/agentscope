"""Redaction of secrets from trace payloads.

Applied when data is recorded onto a span, and again by the repository on every save, so
neither persistence nor an optional exporter ever sees raw secrets. Three mechanisms:

- values under secret-like key names (`api_key`, `AWS_SECRET_ACCESS_KEY`, `githubToken`)
  are replaced wholesale;
- secret-shaped substrings (provider keys, tokens, JWTs, private keys, URL passwords and
  `password=...` assignments) are replaced anywhere in text, object keys included;
- exact values the process knows to be secret (from Settings) are replaced, provided they
  are distinctive enough not to be ordinary words: a database password `agentscope` must
  not rewrite the provider label `agentscope.fake` or a dataset question about AgentScope.

Every pattern runs in linear time, because datasets and model output are untrusted and a
backtracking pattern would let one string stall a worker for hours. Redacted text is also
always storable: NUL and unpaired surrogates become U+FFFD.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from functools import lru_cache
from itertools import pairwise
from typing import Any

from pydantic import BaseModel, SecretStr

REDACTED = "[REDACTED]"
CONFIGURED = "[REDACTED:configured_secret]"
ASSIGNMENT = "[REDACTED:assignment]"

# Configured values shorter than this are never matched; up to the second bound they must
# mix character classes and are matched as whole tokens only; longer ones (keys, tokens,
# passphrases) are matched anywhere, so an escaped or concatenated copy is still caught.
MIN_SECRET_CHARS = 8
SUBSTRING_SECRET_CHARS = 16


@dataclass(frozen=True)
class _ValuePattern:
    kind: str
    # Literals every match contains: a cheap `in` test skips the regex for most text.
    hints: tuple[str, ...]
    pattern: re.Pattern[str]
    replacement: str


# Matches are linear: each starts only where its character class starts (a lookbehind or
# `\b` rules out starts inside a run), every unbounded run is followed by a character
# outside its class or by nothing, and a private-key body never extends past another BEGIN.
_VALUE_PATTERNS: tuple[_ValuePattern, ...] = (
    _ValuePattern(
        "private_key",
        ("-----BEGIN ",),
        re.compile(
            r"-----BEGIN [A-Z ]{0,40}PRIVATE KEY-----(?:(?!-----BEGIN )[\s\S]){0,8000}?"
            r"-----END [A-Z ]{0,40}PRIVATE KEY-----"
        ),
        "[REDACTED:private_key]",
    ),
    _ValuePattern(
        "anthropic_key",
        ("sk-ant-",),
        re.compile(r"\bsk-ant-[A-Za-z0-9_\-]{16,}"),
        "[REDACTED:anthropic_key]",
    ),
    _ValuePattern(
        "openai_key",
        ("sk-",),
        re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_\-]{16,}"),
        "[REDACTED:openai_key]",
    ),
    _ValuePattern(
        "github_token",
        ("ghp_", "gho_", "ghu_", "ghs_", "ghr_", "github_pat_"),
        re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})"),
        "[REDACTED:github_token]",
    ),
    _ValuePattern(
        "aws_access_key",
        ("AKIA", "ASIA"),
        re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),
        "[REDACTED:aws_access_key]",
    ),
    _ValuePattern(
        "slack_token",
        ("xox",),
        re.compile(r"\bxox[abprs]-[A-Za-z0-9\-]{10,}"),
        "[REDACTED:slack_token]",
    ),
    _ValuePattern(
        "jwt",
        ("eyJ",),
        # Not `\b`: '-' is in the class but is a non-word character, so `\b` would let
        # every `eyJ` inside `eyJ-eyJ-...` start a match that rescans to the end.
        re.compile(
            r"(?<![A-Za-z0-9_\-])eyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}"
        ),
        "[REDACTED:jwt]",
    ),
    _ValuePattern(
        "bearer",
        ("earer",),
        re.compile(r"\b[Bb]earer\s+[A-Za-z0-9\-._~+/]{12,}=*"),
        "[REDACTED:bearer]",
    ),
    _ValuePattern(
        "url_password",
        ("://",),
        # scheme://user:password@ — the password only, so the URL stays readable.
        re.compile(
            r"(?<![A-Za-z0-9+.\-])([A-Za-z][A-Za-z0-9+.\-]{0,31}://[^\s/?#@:]{0,256}:)"
            r"[^\s/?#@]{1,256}(?=@)"
        ),
        r"\1[REDACTED:url_password]",
    ),
)
# No pattern, assignment or configured value matches fewer characters than this.
_SHORTEST_MATCH = 6

# `name = value` / `name: value` / `"name": "value"`; the value is redacted when the name is
# secret-like. Names are matched alone, so a harmless `note=` never swallows `password=...`.
_ASSIGNMENT_NAME = re.compile(
    r"(?<![A-Za-z0-9_.\-])([A-Za-z_][A-Za-z0-9_.\-]{0,127})[\"']?[ \t]{0,8}[:=][ \t]{0,8}[\"']?"
)
_ASSIGNED_VALUE = re.compile(r"[^\s\"',;]{4,}")

# Key names are split into words (camelCase, `_`, `-`, `.`, spaces) and matched by word,
# so `SECRET_KEY` and `githubToken` are caught while `max_tokens` and `secretary` are not.
_NAME_WORD = re.compile(r"[A-Z]+(?![a-z])|[A-Z]?[a-z]+|[0-9]+")
_SECRET_WORDS = frozenset(
    {
        "secret",
        "secrets",
        "password",
        "passwords",
        "passwd",
        "passphrase",
        "credential",
        "credentials",
        "authorization",
        "cookie",
        "cookies",
        "bearer",
    }
)
# Adjacent words joined (`api_key`, `ApiKey` and `apikey` all give `apikey`).
_SECRET_COMPOUNDS = frozenset(
    {
        "apikey",
        "clientsecret",
        "privatekey",
        "secretkey",
        "accesskey",
        "signingkey",
        "encryptionkey",
        "masterkey",
        "sessionid",
        "sessiontoken",
        "setcookie",
    }
)

_MARKER = re.compile(r"\[(?:REDACTED|TRUNCATED)(?::[a-z_]+)?\](?:#[0-9]+)?")
_UNSTORABLE = re.compile("[\x00\ud800-\udfff]")

_MAX_DEPTH = 32
_KEY_CACHE_SIZE = 4096


def sanitize_text(text: str) -> str:
    """`text` with NUL and unpaired surrogates replaced by U+FFFD (a surrogate pair split
    into two code points is joined back). PostgreSQL rejects NUL in text and JSON, and a
    lone surrogate cannot be encoded as UTF-8, so either would fail a save or a response."""
    if _UNSTORABLE.search(text) is None:
        return text
    joined = text.encode("utf-16-le", "surrogatepass").decode("utf-16-le", "replace")
    return joined.replace("\x00", "\ufffd")


@lru_cache(maxsize=4096)
def is_secret_name(name: str) -> bool:
    """Whether a key or variable name says its value is a secret. Redaction markers in the
    name are ignored, so a key renamed to `[REDACTED:configured_secret]` keeps its value."""
    words = [w.lower() for w in _NAME_WORD.findall(_MARKER.sub(" ", name))]
    if not words:
        return False
    # Singular: `githubToken` and `accesstoken` hold one, `max_tokens` counts them.
    if words[-1].endswith("token"):
        return True
    joined = {a + b for a, b in pairwise(words)}
    return any(w in _SECRET_WORDS or w in _SECRET_COMPOUNDS for w in words) or bool(
        joined & _SECRET_COMPOUNDS
    )


def _distinctive(value: str) -> bool:
    """Whether a configured value can be redacted by exact match without hitting ordinary
    words: long enough, and not a dictionary-like word or a run of one character class."""
    if len(value) < MIN_SECRET_CHARS or len(set(value)) < 4:
        return False
    if len(value) >= SUBSTRING_SECRET_CHARS:
        return True
    classes = (
        any(c.islower() for c in value),
        any(c.isupper() for c in value),
        any(c.isdigit() for c in value),
        any(not c.isalnum() for c in value),
    )
    return sum(classes) >= 2


class Redactor:
    """Idempotent: redacting already-redacted data changes nothing."""

    def __init__(self, extra_keys: Iterable[str] = (), secret_values: Iterable[str] = ()) -> None:
        self._extra = frozenset(k.lower() for k in extra_keys)
        values = {v for v in secret_values if v}
        usable = {v for v in values if _distinctive(v)}
        # Configured values too plain to match by value (callers may warn about them).
        self.ignored_secrets = len(values - usable)
        self._substring_secrets = tuple(
            sorted((v for v in usable if len(v) >= SUBSTRING_SECRET_CHARS), key=len, reverse=True)
        )
        self._token_secrets = tuple(
            sorted((v for v in usable if len(v) < SUBSTRING_SECRET_CHARS), key=len, reverse=True)
        )
        self._token_pattern = re.compile(
            r"(?<![A-Za-z0-9])(?:"
            + "|".join(re.escape(v) for v in self._token_secrets)
            + r")(?![A-Za-z0-9])"
        )
        self._clean_keys: set[str] = set()

    def is_sensitive_key(self, key: str) -> bool:
        return key.lower() in self._extra or is_secret_name(key)

    def redact_text(self, text: str) -> str:
        text = sanitize_text(text)
        if len(text) < _SHORTEST_MATCH:
            return text
        for secret in self._substring_secrets:
            if secret in text:
                text = text.replace(secret, CONFIGURED)
        if any(secret in text for secret in self._token_secrets):
            text = self._token_pattern.sub(CONFIGURED, text)
        for value in _VALUE_PATTERNS:
            if any(hint in text for hint in value.hints):
                text = value.pattern.sub(value.replacement, text)
        if ":" in text or "=" in text:
            text = self._redact_assignments(text)
        return text

    def _redact_assignments(self, text: str) -> str:
        parts: list[str] = []
        pos = 0
        for match in _ASSIGNMENT_NAME.finditer(text):
            if match.start() < pos or not self.is_sensitive_key(match.group(1)):
                continue
            value = _ASSIGNED_VALUE.match(text, match.end())
            if value is None:
                continue
            parts += [text[pos : match.end()], ASSIGNMENT]
            pos = value.end()
        if not parts:
            return text
        parts.append(text[pos:])
        return "".join(parts)

    def redact(self, value: Any, _depth: int = 0) -> Any:
        if _depth > _MAX_DEPTH:
            return "[TRUNCATED:depth]"
        if isinstance(value, str):
            return self.redact_text(value)
        if isinstance(value, dict):
            return self._redact_mapping(value, _depth)
        if isinstance(value, list | tuple):
            return [self.redact(item, _depth + 1) for item in value]
        return value

    def _redact_key(self, key: str) -> str:
        """`redact_text` for keys. Payloads repeat the same few keys endlessly, so keys found
        clean are remembered (only those: a remembered key never holds a secret)."""
        if key in self._clean_keys:
            return key
        redacted = self.redact_text(key)
        if redacted == key:
            if len(self._clean_keys) >= _KEY_CACHE_SIZE:
                self._clean_keys.clear()
            self._clean_keys.add(key)
        return redacted

    def _redact_mapping(self, value: dict[Any, Any], depth: int) -> dict[str, Any]:
        """Keys are text too: a secret used as a key is renamed to its redaction marker,
        numbered (`#2`, `#3`) when that would collide with another key."""
        items = [(str(k), v) for k, v in value.items()]
        renamed = [self._redact_key(key) for key, _ in items]
        taken = {new for (key, _), new in zip(items, renamed, strict=True) if new == key}
        out: dict[str, Any] = {}
        for (key, item), new in zip(items, renamed, strict=True):
            if new != key:
                base, n = new, 1
                while new in taken:
                    n += 1
                    new = f"{base}#{n}"
                taken.add(new)
            out[new] = REDACTED if self.is_sensitive_key(key) else self.redact(item, depth + 1)
        return out


def settings_secrets(settings: BaseModel) -> list[str]:
    """Every SecretStr value held by `settings`, including those inside dicts, lists and
    nested models (for example each AGENTSCOPE_OTLP_HEADERS value), for
    `Redactor(secret_values=...)`. New secret settings are covered without listing them."""
    found: list[str] = []

    def walk(value: object, depth: int) -> None:
        if depth > _MAX_DEPTH:
            return
        if isinstance(value, SecretStr):
            found.append(value.get_secret_value())
        elif isinstance(value, BaseModel):
            for name in type(value).model_fields:
                walk(getattr(value, name), depth + 1)
        elif isinstance(value, dict):
            for item in value.values():
                walk(item, depth + 1)
        elif isinstance(value, list | tuple | set | frozenset):
            for item in value:
                walk(item, depth + 1)

    walk(settings, 0)
    return found
