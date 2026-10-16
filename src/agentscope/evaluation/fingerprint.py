"""Records what a suite ran on, so two results can be told apart before they are compared."""

from __future__ import annotations

import hashlib
import platform
from importlib import metadata, resources

from agentscope.domain.agents import AgentConfig, ProviderName
from agentscope.domain.evaluation import CHECK_ENGINE_VERSION, EnvironmentFingerprint


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


def environment_fingerprint(config: AgentConfig) -> EnvironmentFingerprint:
    return EnvironmentFingerprint(
        agentscope_version=_version("agentscope"),
        check_engine_version=CHECK_ENGINE_VERSION,
        python_version=platform.python_version(),
        langgraph_version=_version("langgraph"),
        data_hash=data_hash(),
        provider=config.model.provider.value,
        model=config.model.model,
        deterministic=config.model.provider is ProviderName.FAKE,
    )
