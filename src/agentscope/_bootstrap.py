"""Process-level privacy guarantees, applied before any third-party library is imported.

LangGraph depends on langchain-core, which can upload run trees to LangSmith when certain
environment variables are set. AgentScope promises no telemetry, so those switches are
forced off for this process.

OpenTelemetry SDK environment configuration is not used either: AgentScope builds its own
tracer provider and only exports when Settings say so. The variables that the SDK and the
OTLP exporter would still read are removed, because they could redirect export, attach
headers, truncate payload attributes mid-JSON or disable tracing altogether.
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

# The OTLP exporter reads OTEL_EXPORTER_OTLP_* during construction (it merges
# OTEL_EXPORTER_OTLP_HEADERS into explicitly passed headers), span limits read OTEL_SPAN_*
# and OTEL_ATTRIBUTE_*, and the tracer provider reads OTEL_SDK_DISABLED, which no
# constructor argument overrides.
_OTEL_PREFIXES = ("OTEL_EXPORTER_OTLP_", "OTEL_SPAN_", "OTEL_ATTRIBUTE_")
_OTEL_NAMES = frozenset({"OTEL_TRACES_EXPORTER", "OTEL_SDK_DISABLED"})

_applied = False


def remove_otel_settings() -> list[str]:
    """Remove the OpenTelemetry variables AgentScope ignores. Returns their names, sorted."""
    removed = sorted(k for k in os.environ if k in _OTEL_NAMES or k.startswith(_OTEL_PREFIXES))
    for key in removed:
        os.environ.pop(key, None)
    return removed


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
    changed.extend(remove_otel_settings())
    if changed and not _applied:
        # Names only: values can be credentials (API keys, OTLP headers).
        logging.getLogger("agentscope").warning(
            "Ignoring telemetry and OpenTelemetry environment settings (AgentScope sends no "
            "telemetry and configures trace export only through AGENTSCOPE_* settings): %s",
            ", ".join(sorted(changed)),
        )
    # Quiet HTTP client logs that would otherwise print request URLs and headers.
    for name in ("httpx", "httpcore"):
        logging.getLogger(name).setLevel(logging.WARNING)
    _applied = True
    return changed


apply()
