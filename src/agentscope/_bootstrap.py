"""Process-level privacy guarantees, applied before any third-party library is imported.

LangGraph depends on langchain-core, which can upload run trees to LangSmith when certain
environment variables are set. AgentScope promises no telemetry, so those switches are
forced off for this process. OpenTelemetry SDK environment configuration is not used:
AgentScope builds its own tracer provider and only exports when Settings say so.
"""

from __future__ import annotations

import logging
import os

_FORCED = {
    "LANGSMITH_TRACING": "false",
    "LANGCHAIN_TRACING_V2": "false",
    "LANGCHAIN_TRACING": "false",
}
_REMOVED = ("LANGSMITH_API_KEY", "LANGCHAIN_API_KEY", "LANGSMITH_ENDPOINT", "LANGCHAIN_ENDPOINT")

_applied = False


def apply() -> list[str]:
    """Idempotently disable third-party telemetry. Returns the variables it overrode."""
    global _applied
    changed: list[str] = []
    for key, value in _FORCED.items():
        if os.environ.get(key, value).lower() != value:
            changed.append(key)
        os.environ[key] = value
    for key in _REMOVED:
        if os.environ.pop(key, None) is not None:
            changed.append(key)
    if changed and not _applied:
        logging.getLogger("agentscope").warning(
            "Ignoring third-party telemetry settings (AgentScope sends no telemetry): %s",
            ", ".join(sorted(changed)),
        )
    # Quiet HTTP client logs that would otherwise print request URLs and headers.
    for name in ("httpx", "httpcore"):
        logging.getLogger(name).setLevel(logging.WARNING)
    _applied = True
    return changed


apply()
