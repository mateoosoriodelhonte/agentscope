"""The shared services layer: startup, secrets, datasets, and suites that cannot finish."""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from pathlib import Path

import pytest
from apiclient import BASE_URL, post
from fastapi.testclient import TestClient
from pydantic import SecretStr

from agentscope.api.app import create_app
from agentscope.config import Settings
from agentscope.datasets.issues import DatasetError
from agentscope.datasets.packaged import builtin_datasets, load_builtin_dataset
from agentscope.domain.common import utcnow
from agentscope.domain.datasets import Dataset
from agentscope.evaluation.checks import CHECKS
from agentscope.evaluation.fingerprint import environment_fingerprint
from agentscope.runtime.runner import AgentRunner
from agentscope.services import Services, build_redactor, build_services, egress_destinations
from agentscope.storage.db import upgrade
from agentscope.storage.models import SuiteRunRow
from agentscope.storage.repository import SuiteHeader

RESEARCH = load_builtin_dataset("research-assistant")


def test_redactor_knows_every_configured_secret(tmp_path: Path) -> None:
    settings = Settings(
        data_dir=tmp_path,
        api_token=SecretStr("api-token-value-0123456789"),
        openai_compatible_api_key=SecretStr("provider-key-value-0123"),
        database_url=SecretStr("postgresql://scope:db-password-0123@db.internal/scope"),
    )
    redact = build_redactor(settings).redact_text
    for secret in ("api-token-value-0123456789", "provider-key-value-0123", "db-password-0123"):
        assert secret not in redact(f"leaked {secret} here")
    assert "db-password-0123" not in settings.database_location


def test_egress_lists_targets_and_refusals(tmp_path: Path) -> None:
    assert egress_destinations(Settings(data_dir=tmp_path, enable_ollama=False)) == []
    destinations = egress_destinations(
        Settings(data_dir=tmp_path, otlp_endpoint="http://collector.example.com:4318")
    )
    assert destinations[0] == "ollama: http://127.0.0.1:11434"
    assert destinations[1].startswith("otlp: refused (")


def test_startup_marks_interrupted_work_and_registers_builtins(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path, enable_ollama=False)
    first = build_services(settings)
    names = {(d.name, d.version) for d in first.repository.list_datasets()}
    assert {(d.name, d.version) for d in builtin_datasets()} <= names
    # A crashed process left this suite `running`.
    config = first.catalog.resolve("research_assistant@v2")
    suite_id = str(uuid.uuid4())
    first.repository.create_suite(
        SuiteHeader(
            suite_id=suite_id,
            dataset_name=RESEARCH.name,
            dataset_version=RESEARCH.version,
            dataset_hash=RESEARCH.content_hash(),
            agent_config=config,
            config_hash=config.config_hash(),
            effective_config_hash=first.runner.effective_config_hash(config),
            checks_enabled=sorted(CHECKS),
            environment=environment_fingerprint(config),
            started_at=utcnow(),
        )
    )
    first.repository.engine.dispose()

    second = build_services(settings)  # the restart
    suite = second.repository.get_suite(suite_id)
    assert suite is not None
    assert suite.status == "error"
    assert suite.error is not None
    assert suite.error.startswith("interrupted")
    assert len(second.repository.list_datasets()) == len(names)  # registration is idempotent
    second.repository.engine.dispose()


def test_a_conflicting_builtin_is_kept_with_a_warning(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    settings = Settings(data_dir=tmp_path, enable_ollama=False)
    services = build_services(settings, migrate=False)
    upgrade(services.repository.engine)
    older = RESEARCH.model_copy(update={"description": "an older release"})
    services.repository.save_dataset(older)
    with caplog.at_level(logging.WARNING):
        services.register_builtin_datasets()
    assert "built-in dataset not registered" in caplog.text
    stored = services.repository.get_dataset(RESEARCH.name, RESEARCH.version)
    assert stored is not None
    assert stored.dataset.description == "an older release"
    services.repository.engine.dispose()


def test_builtin_datasets_import_unchanged(services: Services) -> None:
    for dataset in builtin_datasets():
        assert [i for i in services.validate_dataset(dataset) if i.severity == "error"] == []
        stored = services.repository.get_dataset(dataset.name, dataset.version)
        assert stored is not None
        assert stored.summary.content_hash == dataset.content_hash()
        assert stored.dataset == dataset  # argument matchers survive the round trip


def test_import_refuses_rewritten_content(services: Services) -> None:
    case = RESEARCH.cases[0].model_copy(
        update={"forbidden_output_substrings": ["Bearer abcdefghijklmnopqrstuvwxyz"]}
    )
    leaky = Dataset(name="leaky", agent="research_assistant", cases=[case])
    with pytest.raises(DatasetError, match="synthetic canaries"):
        services.import_dataset(leaky)
    assert services.repository.get_dataset("leaky", "1") is None


async def test_a_cancelled_suite_is_marked_errored(services: Services) -> None:
    started = asyncio.Event()
    task = asyncio.create_task(
        services.run_suite(
            RESEARCH,
            services.catalog.resolve("research_assistant@v2"),
            suite_id="7a8f2b4e-1d4c-4a7e-9c55-1f3e8b2d6a90",
            progress=lambda done, _total: started.set(),
        )
    )
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    suite = services.repository.get_suite("7a8f2b4e-1d4c-4a7e-9c55-1f3e8b2d6a90")
    assert suite is not None
    assert suite.status == "error"
    assert suite.error == "cancelled: AgentScope stopped before the evaluation finished"
    assert 1 <= len(suite.cases) < len(RESEARCH.cases)


def test_server_shutdown_cancels_a_running_evaluation(
    services: Services, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = AgentRunner.run
    calls = 0

    async def stall_after_first(self: AgentRunner, request: object) -> object:
        nonlocal calls
        calls += 1
        if calls > 1:
            await asyncio.Event().wait()
        return await original(self, request)  # type: ignore[arg-type]

    monkeypatch.setattr(AgentRunner, "run", stall_after_first)
    with TestClient(create_app(services, web_dist=tmp_path / "none"), base_url=BASE_URL) as c:
        accepted = post(
            c,
            "/api/evaluations",
            {
                "agent_ref": "research_assistant@v2",
                "dataset": {"name": RESEARCH.name, "version": "1"},
            },
        ).json()
        deadline = time.monotonic() + 30
        while c.get(f"/api/jobs/{accepted['job_id']}").json()["progress"] < 1:
            assert time.monotonic() < deadline
            time.sleep(0.01)
    suite = services.repository.get_suite(accepted["resource_id"])
    assert suite is not None
    assert suite.status == "error"
    assert suite.error is not None
    assert suite.error.startswith("cancelled")
    with services.repository.session() as session:
        row = session.get(SuiteRunRow, uuid.UUID(accepted["resource_id"]))
        assert row is not None
        assert row.finished_at is not None


def test_agents_endpoint(client: TestClient) -> None:
    body = client.get("/api/agents").json()
    names = [agent["name"] for agent in body["agents"]]
    assert names == ["research_assistant", "task_planner"]
    research = body["agents"][0]
    assert [v["version"] for v in research["versions"]] == ["v1", "v2", "v3"]
    assert research["output_schema"]["type"] == "object"
    assert {tool["name"] for tool in body["tools"]} >= {"search_documents", "lookup_record"}
