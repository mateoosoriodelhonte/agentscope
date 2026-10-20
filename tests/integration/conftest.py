from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from apiclient import BASE_URL
from fastapi.testclient import TestClient

from agentscope.api.app import create_app
from agentscope.config import Settings
from agentscope.datasets.packaged import load_builtin_dataset
from agentscope.domain.datasets import Dataset
from agentscope.services import Services, build_services

# Research cases v1 fails and v2 passes, each for another reason: the instruction-hierarchy
# prompt with isolation (together only), the verified-only search schema, a tool retry.
ABLATION_CASES = ("injection-encoded-hex", "qa-transit-fare", "fault-timeout-transient")


@pytest.fixture
def small_research() -> Dataset:
    """Three research cases: enough to ablate v1 -> v2 in a second, not in fifteen."""
    research = load_builtin_dataset("research-assistant")
    return Dataset(
        name="ablation-small",
        agent=research.agent,
        cases=[case for case in research.cases if case.id in ABLATION_CASES],
    )


@pytest.fixture
def services(tmp_path: Path) -> Iterator[Services]:
    """Like the root fixture, but with Ollama disabled so no test probes a local server."""
    svc = build_services(Settings(data_dir=tmp_path / "svc", enable_ollama=False))
    yield svc
    svc.repository.engine.dispose()


@pytest.fixture
def client(services: Services, tmp_path: Path) -> Iterator[TestClient]:
    with TestClient(create_app(services, web_dist=tmp_path / "no-dist"), base_url=BASE_URL) as c:
        yield c
