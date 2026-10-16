from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest

from agentscope.agents.base import AgentCatalog, default_catalog
from agentscope.config import Settings
from agentscope.providers.fake import FakeModelProvider
from agentscope.runtime.runner import AgentRunner
from agentscope.services import Services, build_services
from agentscope.tools.registry import ToolRegistry, default_registry


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # Never touch a developer's real ./.agentscope or environment configuration.
    for key in list(os.environ):
        if key.startswith("AGENTSCOPE_") and key != "AGENTSCOPE_TEST_POSTGRES_URL":
            monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("AGENTSCOPE_DATA_DIR", str(tmp_path / "data"))
    from agentscope.config import get_settings

    get_settings.cache_clear()


@pytest.fixture
def catalog() -> AgentCatalog:
    return default_catalog()


@pytest.fixture
def registry() -> ToolRegistry:
    return default_registry()


@pytest.fixture
def runner(catalog: AgentCatalog, registry: ToolRegistry) -> AgentRunner:
    return AgentRunner(catalog, registry, {"fake": FakeModelProvider(catalog.brains())})


@pytest.fixture
def services(tmp_path: Path) -> Iterator[Services]:
    settings = Settings(data_dir=tmp_path / "svc")
    svc = build_services(settings)
    yield svc
    svc.repository.engine.dispose()
