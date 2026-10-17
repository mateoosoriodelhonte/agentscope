"""Environment fingerprint and packaged-data hash."""

from __future__ import annotations

import hashlib
import platform
from pathlib import Path
from typing import Any

import agentscope
from agentscope.agents.base import AgentCatalog
from agentscope.domain.agents import ModelConfig, ProviderName
from agentscope.domain.evaluation import CHECK_ENGINE_VERSION, ResolvedContent
from agentscope.domain.tools import ToolSpec
from agentscope.evaluation.fingerprint import data_hash, environment_fingerprint, resolved_content


def test_data_hash_covers_every_packaged_json_file_in_name_order() -> None:
    data_dir = Path(agentscope.__file__).parent / "tools" / "data"
    digest = hashlib.sha256()
    for path in sorted(data_dir.glob("*.json"), key=lambda p: p.name):
        content = path.read_bytes()
        digest.update(f"{path.name}\0{len(content)}\0".encode())
        digest.update(content)
    assert data_hash() == f"sha256:{digest.hexdigest()}"
    assert data_hash() == data_hash()


def test_fingerprint_marks_only_the_fake_provider_deterministic(catalog: AgentCatalog) -> None:
    config = catalog.resolve("research_assistant@v2")
    fingerprint = environment_fingerprint(config)
    assert fingerprint.deterministic is True
    assert (fingerprint.provider, fingerprint.model) == ("fake", "scripted-naive")
    assert fingerprint.check_engine_version == CHECK_ENGINE_VERSION
    assert fingerprint.python_version == platform.python_version()
    assert fingerprint.langgraph_version not in ("", "unknown")

    ollama = config.model_copy(
        update={"model": ModelConfig(provider=ProviderName.OLLAMA, model="llama3.2:3b")}
    )
    assert environment_fingerprint(ollama).deterministic is False
    assert fingerprint.resolved is None


def _specs(description: str = "Search the knowledge base.") -> list[ToolSpec]:
    return [
        ToolSpec(name="search_documents", description=description, parameters={"type": "object"}),
        ToolSpec(name="retrieve_document", description="Read one.", parameters={"type": "object"}),
    ]


def _resolved(**changes: Any) -> ResolvedContent:
    inputs: dict[str, Any] = {
        "prompt": "You are a research assistant.",
        "tool_specs": _specs(),
        "output_schema": {"type": "object"},
        "graph": [("START", "plan"), ("plan", "finish")],
        "fake_brain_version": "2",
    }
    return resolved_content(**{**inputs, **changes})


def test_resolved_content_hashes_each_component_separately() -> None:
    base = _resolved()
    assert base == _resolved()
    assert sorted(base.tools) == ["retrieve_document", "search_documents"]
    assert base.fake_brain_version == "2"
    edited = _resolved(tool_specs=_specs("Search the archive."))
    assert edited.tools["search_documents"] != base.tools["search_documents"]
    assert edited.tools["retrieve_document"] == base.tools["retrieve_document"]
    assert (edited.prompt, edited.output_schema, edited.graph) == (
        base.prompt,
        base.output_schema,
        base.graph,
    )
    assert _resolved(prompt="Changed.").prompt != base.prompt
    assert _resolved(graph=[("START", "plan")]).graph != base.graph
    assert _resolved(fake_brain_version=None).fake_brain_version is None


def test_fingerprint_records_resolved_content_when_given(catalog: AgentCatalog) -> None:
    config = catalog.resolve("research_assistant@v2")
    assert environment_fingerprint(config, resolved=_resolved()).resolved == _resolved()
