"""The import-time bootstrap: third-party telemetry off, OpenTelemetry environment ignored,
and only variable names (never values) reported."""

from __future__ import annotations

import logging
import os
import subprocess
import sys

import pytest

from agentscope import _bootstrap

PLANTED = "planted-credential-value"
OTEL_IGNORED = {
    "OTEL_EXPORTER_OTLP_ENDPOINT": "http://203.0.113.7:4318",
    "OTEL_EXPORTER_OTLP_HEADERS": f"authorization=Bearer {PLANTED}",
    "OTEL_EXPORTER_OTLP_TRACES_HEADERS": f"x-api-key={PLANTED}",
    "OTEL_TRACES_EXPORTER": "otlp",
    "OTEL_SPAN_ATTRIBUTE_VALUE_LENGTH_LIMIT": "8",
    "OTEL_ATTRIBUTE_COUNT_LIMIT": "1",
    "OTEL_SDK_DISABLED": "true",
}


@pytest.fixture
def first_start(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pretend the bootstrap has not run yet in this process (it warns only once)."""
    monkeypatch.setattr(_bootstrap, "_applied", False)


@pytest.mark.usefixtures("first_start")
def test_apply_removes_otel_settings_and_names_them_without_values(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    for key, value in OTEL_IGNORED.items():
        monkeypatch.setenv(key, value)
    # Not read by AgentScope (its tracer provider gets an explicit resource), so left alone,
    # as are variables that merely contain a matching substring.
    monkeypatch.setenv("OTEL_SERVICE_NAME", "kept")
    monkeypatch.setenv("MY_OTEL_EXPORTER_OTLP_HEADERS", "kept")

    with caplog.at_level(logging.WARNING, logger="agentscope"):
        changed = _bootstrap.apply()

    assert set(OTEL_IGNORED) <= set(changed)
    assert not set(OTEL_IGNORED) & set(os.environ)
    assert os.environ["OTEL_SERVICE_NAME"] == "kept"
    assert os.environ["MY_OTEL_EXPORTER_OTLP_HEADERS"] == "kept"
    [record] = [r for r in caplog.records if r.name == "agentscope"]
    message = record.getMessage()
    assert all(key in message for key in OTEL_IGNORED)
    assert PLANTED not in caplog.text


@pytest.mark.usefixtures("first_start")
def test_apply_is_idempotent(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_HEADERS", f"authorization=Bearer {PLANTED}")
    first = _bootstrap.apply()
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="agentscope"):
        second = _bootstrap.apply()

    assert "OTEL_EXPORTER_OTLP_HEADERS" in first
    assert second == []
    assert not [r for r in caplog.records if r.name == "agentscope"]
    assert "OTEL_EXPORTER_OTLP_HEADERS" not in os.environ
    assert os.environ["LANGSMITH_TRACING"] == "false"


def test_remove_otel_settings_returns_sorted_names(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OTEL_TRACES_EXPORTER", "console")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_TIMEOUT", "1")
    removed = _bootstrap.remove_otel_settings()
    assert removed == ["OTEL_EXPORTER_OTLP_TIMEOUT", "OTEL_TRACES_EXPORTER"]
    assert _bootstrap.remove_otel_settings() == []


def test_importing_agentscope_removes_otel_settings() -> None:
    env = {k: v for k, v in os.environ.items() if not k.startswith(("OTEL_", "AGENTSCOPE_"))}
    env.update(OTEL_IGNORED)
    code = "import os, agentscope; print(sorted(k for k in os.environ if k.startswith('OTEL_')))"
    result = subprocess.run(
        [sys.executable, "-c", code],
        env=env,
        capture_output=True,
        text=True,
        check=True,
        timeout=60,
    )
    assert result.stdout.strip() == "[]"
    assert all(key in result.stderr for key in OTEL_IGNORED)
    assert PLANTED not in result.stdout + result.stderr
