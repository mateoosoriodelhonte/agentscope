"""Opt-in end-to-end run against a real local Ollama server.

Skipped unless AGENTSCOPE_TEST_OLLAMA_MODEL names a model you have already pulled, e.g.

    AGENTSCOPE_TEST_OLLAMA_MODEL=llama3.2 uv run pytest -m ollama

AGENTSCOPE_TEST_OLLAMA_URL overrides the server (default http://127.0.0.1:11434; it must be
loopback, which is also all pytest-socket allows). The variables are read at import time
because the suite's autouse fixture clears AGENTSCOPE_* variables before each test.
"""

from __future__ import annotations

import os

import pytest

from agentscope.agents.base import AgentCatalog
from agentscope.config import Settings
from agentscope.domain.agents import Limits, ModelConfig, ProviderName
from agentscope.domain.traces import RunStatus, SpanKind
from agentscope.providers.registry import build_providers
from agentscope.runtime.runner import AgentRunner, RunRequest
from agentscope.tools.registry import ToolRegistry

MODEL = os.environ.get("AGENTSCOPE_TEST_OLLAMA_MODEL", "")
URL = os.environ.get("AGENTSCOPE_TEST_OLLAMA_URL", "http://127.0.0.1:11434")

pytestmark = [
    pytest.mark.ollama,
    pytest.mark.skipif(not MODEL, reason="set AGENTSCOPE_TEST_OLLAMA_MODEL to run against Ollama"),
]


async def test_research_assistant_end_to_end_on_ollama(
    catalog: AgentCatalog, registry: ToolRegistry
) -> None:
    runner = AgentRunner(catalog, registry, build_providers(Settings(ollama_url=URL), catalog))
    config = catalog.resolve("research_assistant@v2").model_copy(
        update={
            "model": ModelConfig(provider=ProviderName.OLLAMA, model=MODEL, request_timeout_s=300),
            "limits": Limits(run_timeout_s=600, tool_timeout_s=10),
        }
    )
    trace = await runner.run(
        RunRequest(config=config, task="In what year did the Meridian Footbridge open?")
    )

    # A real model may answer well or badly; the provider path itself must work.
    assert trace.error_code not in {"provider_error", "provider_timeout"}, trace.error
    assert trace.status is not RunStatus.ERROR, trace.error
    assert trace.provider == "ollama"
    llm_spans = [s for s in trace.spans if s.kind is SpanKind.LLM]
    assert llm_spans
    assert trace.steps >= 1
