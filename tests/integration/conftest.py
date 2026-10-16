from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from apiclient import BASE_URL
from fastapi.testclient import TestClient

from agentscope.api.app import create_app
from agentscope.config import Settings
from agentscope.services import Services, build_services


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
