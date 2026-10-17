"""Records what a suite ran on, so two results can be told apart before they are compared."""

from __future__ import annotations

import hashlib
import platform
from collections.abc import Mapping, Sequence
from importlib import metadata, resources
from typing import Any

from agentscope.domain.agents import AgentConfig, ProviderName
from agentscope.domain.common import content_hash
from agentscope.domain.evaluation import (
    CHECK_ENGINE_VERSION,
    EnvironmentFingerprint,
    ResolvedContent,
)
from agentscope.domain.tools import ToolSpec


def _version(distribution: str) -> str:
    try:
        return metadata.version(distribution)
    except metadata.PackageNotFoundError:
        return "unknown"


def data_hash() -> str:
    """sha256 over the packaged synthetic tool data (tools/data/*.json, by file name).

    Read as package resources relative to `agentscope`, so the evaluator imports nothing
    from the tools package.
    """
    data = resources.files("agentscope").joinpath("tools", "data")
    files = sorted((f for f in data.iterdir() if f.name.endswith(".json")), key=lambda f: f.name)
    digest = hashlib.sha256()
    for file in files:
        content = file.read_bytes()
        digest.update(f"{file.name}\0{len(content)}\0".encode())
        digest.update(content)
    return f"sha256:{digest.hexdigest()}"


def resolved_content(
    *,
    prompt: str,
    tool_specs: Sequence[ToolSpec],
    output_schema: Mapping[str, Any],
    graph: Sequence[Sequence[str]],
    fake_brain_version: str | None,
) -> ResolvedContent:
    """Hash each component that `effective_config_hash` covers besides the configuration
    itself, from the same inputs, so a comparison can tell which one changed. Takes plain
    values: the caller resolves them (the evaluator imports nothing from the runtime)."""
    return ResolvedContent(
        prompt=content_hash(prompt),
        tools={spec.name: content_hash(spec.model_dump(mode="json")) for spec in tool_specs},
        output_schema=content_hash(dict(output_schema)),
        graph=content_hash([list(edge) for edge in graph]),
        fake_brain_version=fake_brain_version,
    )


def environment_fingerprint(
    config: AgentConfig, resolved: ResolvedContent | None = None
) -> EnvironmentFingerprint:
    return EnvironmentFingerprint(
        agentscope_version=_version("agentscope"),
        check_engine_version=CHECK_ENGINE_VERSION,
        python_version=platform.python_version(),
        langgraph_version=_version("langgraph"),
        data_hash=data_hash(),
        provider=config.model.provider.value,
        model=config.model.model,
        deterministic=config.model.provider is ProviderName.FAKE,
        resolved=resolved,
    )
