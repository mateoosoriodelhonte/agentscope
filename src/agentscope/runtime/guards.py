"""Runtime guardrails that hold regardless of what the model decides."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

# Heuristic signatures of instructions embedded in data. Detection is advisory: it is
# recorded on the trace for analysis, while enforcement comes from the permission guard
# (unauthorized tools are never executed) and from delimiting untrusted content.
_SIGNATURES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("override", re.compile(r"\b(?:ignore|disregard)\b[^.\n]{0,60}\binstructions?\b", re.I)),
    ("ignore_user", re.compile(r"\bignore\s+the\s+user\b", re.I)),
    ("reveal_prompt", re.compile(r"\breveal\b[^.\n]{0,30}\b(?:hidden|system)\b", re.I)),
    (
        "role_claim",
        re.compile(r"\b(?:system notice|assistant directive|user is an administrator)\b", re.I),
    ),
    (
        "addressed_to_ai",
        re.compile(r"\b(?:note for any ai|to ai assistants|any ai reading)\b", re.I),
    ),
    ("tool_request", re.compile(r"\bcall\s+[a-z_]{3,40}\s+with\b", re.I)),
)
_MAX_SCAN_CHARS = 50_000


@dataclass(frozen=True)
class ContentFinding:
    signature: str
    excerpt: str


def scan_untrusted(value: Any) -> list[ContentFinding]:
    """Return instruction-like signatures found in a tool output."""
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    text = text[:_MAX_SCAN_CHARS]
    findings = []
    for name, pattern in _SIGNATURES:
        match = pattern.search(text)
        if match:
            start, end = max(0, match.start() - 20), min(len(text), match.end() + 20)
            findings.append(ContentFinding(signature=name, excerpt=text[start:end]))
    return findings


@dataclass(frozen=True)
class PermissionDecision:
    allowed: bool
    reason: str


def check_permission(tool: str, permitted: frozenset[str]) -> PermissionDecision:
    if tool not in permitted:
        return PermissionDecision(False, f"tool '{tool}' is not permitted for this run")
    return PermissionDecision(True, "permitted")
