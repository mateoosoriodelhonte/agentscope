"""Environment fingerprint and packaged-data hash."""

from __future__ import annotations

import hashlib
import platform
from pathlib import Path

import agentscope
from agentscope.agents.base import AgentCatalog
from agentscope.domain.agents import ModelConfig, ProviderName
from agentscope.domain.evaluation import CHECK_ENGINE_VERSION
from agentscope.evaluation.fingerprint import data_hash, environment_fingerprint


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
