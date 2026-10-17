"""The shared services layer: startup, secrets, datasets, and suites that cannot finish."""

from __future__ import annotations

import asyncio
import logging
import subprocess
import sys
import time
import uuid
from datetime import timedelta
from pathlib import Path

import pytest
from apiclient import BASE_URL, post, wait_for_job
from fastapi.testclient import TestClient
from pydantic import SecretStr

from agentscope import services as services_module
from agentscope.api.app import create_app
from agentscope.config import Settings
from agentscope.datasets.issues import DatasetError
from agentscope.datasets.packaged import builtin_datasets, load_builtin_dataset
from agentscope.domain.common import utcnow
from agentscope.domain.datasets import Dataset, ExpectedAnswer, MatchMode
from agentscope.evaluation.checks import CHECKS
from agentscope.evaluation.fingerprint import environment_fingerprint
from agentscope.runtime.runner import AgentRunner
from agentscope.services import Services, build_redactor, build_services, egress_destinations
from agentscope.storage.db import upgrade
from agentscope.storage.liveness import this_host
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


def dead_owner() -> str:
    """The owner string of a process on this host that has exited (and been reaped)."""
    child = subprocess.Popen([sys.executable, "-c", ""])
    child.wait()
    return f"{this_host()}:{child.pid}"


def start_suite(services: Services, owner: str | None = None) -> str:
    """A `running` suite, owned by this process or by `owner`."""
    config = services.catalog.resolve("research_assistant@v2")
    suite_id = str(uuid.uuid4())
    services.repository.create_suite(
        SuiteHeader(
            suite_id=suite_id,
            dataset_name=RESEARCH.name,
            dataset_version=RESEARCH.version,
            dataset_hash=RESEARCH.content_hash(),
            agent_config=config,
            config_hash=config.config_hash(),
            effective_config_hash=services.runner.effective_config_hash(config),
            checks_enabled=sorted(CHECKS),
            environment=environment_fingerprint(config),
            started_at=utcnow(),
        )
    )
    if owner is not None:
        with services.repository.session() as session, session.begin():
            row = session.get(SuiteRunRow, uuid.UUID(suite_id))
            assert row is not None
            row.owner = owner
    return suite_id


def status(services: Services, suite_id: str) -> str:
    suite = services.repository.get_suite(suite_id)
    assert suite is not None
    return suite.status


def test_startup_registers_builtins_and_never_touches_running_work(tmp_path: Path) -> None:
    """Every command builds services, read-only ones included: building them must not mark
    another process's live suite as interrupted."""
    settings = Settings(data_dir=tmp_path, enable_ollama=False)
    first = build_services(settings)
    names = {(d.name, d.version) for d in first.repository.list_datasets()}
    assert {(d.name, d.version) for d in builtin_datasets()} <= names
    live = start_suite(first)  # this process is running it
    dead = start_suite(first, owner=dead_owner())
    first.repository.engine.dispose()

    second = build_services(settings)  # another command starting while the suite runs
    assert (status(second, live), status(second, dead)) == ("running", "running")
    assert len(second.repository.list_datasets()) == len(names)  # registration is idempotent

    # Recovery (the server, commands that start work) marks only the suite whose process died.
    assert second.recover_interrupted() == {"runs": 0, "suite_runs": 1}
    assert (status(second, live), status(second, dead)) == ("running", "error")
    suite = second.repository.get_suite(dead)
    assert suite is not None
    assert suite.error is not None
    assert suite.error.startswith("interrupted")
    second.repository.engine.dispose()


def test_the_server_marks_work_whose_process_died(services: Services, tmp_path: Path) -> None:
    live = start_suite(services)
    dead = start_suite(services, owner=dead_owner())
    with TestClient(create_app(services, web_dist=tmp_path / "none"), base_url=BASE_URL) as c:
        assert c.get(f"/api/evaluations/{dead}").json()["status"] == "error"
        assert c.get(f"/api/evaluations/{live}").json()["status"] == "running"


async def test_a_running_suite_sends_heartbeats(
    services: Services, monkeypatch: pytest.MonkeyPatch
) -> None:
    """However long a case takes, the suite's owner keeps telling other processes it lives."""
    monkeypatch.setattr(services_module, "HEARTBEAT_INTERVAL", timedelta(milliseconds=5))
    beats: list[str] = []
    monkeypatch.setattr(services.repository, "heartbeat", beats.append)
    original = AgentRunner.run

    async def slow(self: AgentRunner, request: object) -> object:
        await asyncio.sleep(0.05)
        return await original(self, request)  # type: ignore[arg-type]

    monkeypatch.setattr(AgentRunner, "run", slow)
    one_case = RESEARCH.model_copy(update={"cases": RESEARCH.cases[:1]})
    result = await services.run_suite(one_case, services.catalog.resolve("research_assistant@v2"))
    assert len(beats) >= 2
    assert set(beats) == {result.suite_id}
    count = len(beats)
    await asyncio.sleep(0.05)
    assert len(beats) == count  # stopped with the suite


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


def test_import_refuses_a_secret_shaped_key_without_echoing_it(services: Services) -> None:
    key = "sk-ant-api03-" + "Q" * 24
    case = RESEARCH.cases[0].model_copy(
        update={"expected": ExpectedAnswer(match=MatchMode.EXACT, value={key: None})}
    )
    leaky = Dataset(name="leaky-key", agent="research_assistant", cases=[case])
    with pytest.raises(DatasetError) as info:
        services.import_dataset(leaky)
    assert "synthetic canaries" in str(info.value)
    assert key not in str(info.value)
    assert services.repository.get_dataset("leaky-key", "1") is None


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

    # The fingerprints a suite of each built-in version records, so the dashboard can find
    # that version's suites without guessing.
    assert set(research["version_hashes"]) == {"v1", "v2", "v3"}
    suite = evaluated(client, "research_assistant@v2")
    assert research["version_hashes"]["v2"] == {
        "config_hash": suite["config_hash"],
        "effective_config_hash": suite["effective_config_hash"],
    }


def evaluated(client: TestClient, agent_ref: str) -> dict[str, object]:
    accepted = post(
        client,
        "/api/evaluations",
        {"agent_ref": agent_ref, "dataset": {"name": RESEARCH.name, "version": RESEARCH.version}},
    ).json()
    assert wait_for_job(client, accepted["job_id"])["state"] == "succeeded"
    suite: dict[str, object] = client.get(f"/api/evaluations/{accepted['resource_id']}").json()
    return suite


@pytest.mark.parametrize(
    ("raw", "shown"),
    [
        ("http://127.0.0.1:11434", "http://127.0.0.1:11434"),
        ("http://admin:pw-0123@127.0.0.1:11434/v1", "http://***@127.0.0.1:11434/v1"),
        ("https://host.example/v1?api_key=k-0123#frag", "https://host.example/v1"),
        ("admin:pw-0123@host.example:8080", "***@host.example:8080"),  # no scheme
        ("http://u:pw-0123@host:notaport/x", "http://***@host:notaport/x"),
        # Passwords containing '/', '?' or '#' used to end the authority early.
        ("http://admin:Sup3r/Secr3t@gpu-box:11434", "http://***@gpu-box:11434"),
        ("bob:pa/ss@llm.example.com:8080/v1", "***@llm.example.com:8080/v1"),  # no scheme
        # An '@' after '?' or '#' ends either a password or a query value: hide both.
        ("https://bob:hunter2#x9@llm.example.com/v1", "https://***"),
        ("https://bob:pa?ss@llm.example.com/v1?k=1", "https://***"),
        ("http://h:1/v1?user=a@b.example&key=k-0123", "http://***"),
        ("http://u:pw-0123@h:1/v1?user=a@b.example&key=k-0123", "http://***"),
        ("http://h:1/v1#a@b&token=k-0123", "http://***"),
        ("http://u:pw-0123@h:1/v1/x@y?k=k-0123", "http://***@y"),
    ],
)
def test_display_url_never_shows_credentials_or_queries(raw: str, shown: str) -> None:
    from agentscope.services import display_url

    assert display_url(raw) == shown


def test_tracing_export_is_installed_only_when_configured(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from agentscope import services as services_module

    installed: list[object] = []
    monkeypatch.setattr(services_module, "_tracing_installed", False)
    monkeypatch.setattr(services_module, "configure_pipeline", installed.append)
    monkeypatch.setattr(services_module, "build_pipeline", lambda settings: "pipeline")
    assert services_module.install_tracing(Settings(data_dir=tmp_path)) is False
    configured = Settings(data_dir=tmp_path, otlp_endpoint="http://127.0.0.1:4318")
    assert services_module.install_tracing(configured) is True
    assert services_module.install_tracing(configured) is False  # once per process
    assert installed == ["pipeline"]


def test_otlp_header_values_are_redacted(tmp_path: Path) -> None:
    from agentscope.services import build_redactor

    settings = Settings(data_dir=tmp_path, otlp_headers={"x-collector-key": "collector-secret-123"})
    assert "collector-secret-123" not in build_redactor(settings).redact_text(
        "sent collector-secret-123"
    )


def test_display_copies_survive_markers_longer_than_the_secret(services: Services) -> None:
    """What the CLI prints and the API returns goes through the same redaction as storage,
    so a check message cut to its limit before redaction must not crash it either."""
    from agentscope.domain.evaluation import (
        CaseResult,
        CheckName,
        CheckResult,
        CheckStatus,
        ReasonCode,
    )

    key = "AKIAABCDEFGHIJKLMNOP"
    case = CaseResult(
        case_id="c",
        case_hash="sha256:" + "c" * 64,
        run_id=None,
        trace_id=None,
        status=None,
        passed=False,
        checks=[
            CheckResult(
                name=CheckName.ANSWER_CORRECTNESS,
                status=CheckStatus.FAILED,
                reason=ReasonCode.MISMATCH,
                message="x" * 979 + " " + key,
            )
        ],
    )
    shown = services.redacted(case)
    assert len(shown.checks[0].message) == 1000
    assert key not in shown.model_dump_json()
