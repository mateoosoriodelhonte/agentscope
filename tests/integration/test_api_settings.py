"""Settings view, purge, remote token mode, and JSON-only errors."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from apiclient import BASE_URL, MUTATION, post, run_job
from fastapi.testclient import TestClient
from pydantic import SecretStr

from agentscope.api.app import create_app
from agentscope.config import Settings
from agentscope.services import Services, build_services
from agentscope.storage.repository import PurgeIncomplete, PurgeResult

API_KEY = "sk-local-test-key-0123456789"
TOKEN = "remote-token-for-tests-0123456789"


@pytest.fixture
def configured(tmp_path: Path) -> Iterator[Services]:
    settings = Settings(
        data_dir=tmp_path / "cfg",
        enable_ollama=True,
        enable_openai_compatible=True,
        openai_compatible_url="http://127.0.0.1:8080/v1",
        openai_compatible_api_key=SecretStr(API_KEY),
        otlp_endpoint="https://collector.example.com:4318",
        api_token=SecretStr(TOKEN),
    )
    svc = build_services(settings)
    yield svc
    svc.repository.engine.dispose()


def test_settings_view_masks_secrets_and_lists_egress(configured: Services, tmp_path: Path) -> None:
    with TestClient(create_app(configured, web_dist=tmp_path / "none"), base_url=BASE_URL) as c:
        response = c.get("/api/settings")
    assert response.status_code == 200
    text = response.text
    assert API_KEY not in text
    assert TOKEN not in text
    view = response.json()
    assert view["effective"]["openai_compatible_api_key"] == "**********"
    assert view["effective"]["api_token"] == "**********"
    assert view["storage"]["backend"] == "sqlite"
    assert {t["name"] for t in view["storage"]["tables"]} >= {"runs", "spans", "datasets"}
    assert view["egress"][:2] == [
        "ollama: http://127.0.0.1:11434",
        "openai_compatible: http://127.0.0.1:8080/v1",
    ]
    # A non-loopback OTLP endpoint without an allow-list entry is refused, and shown so.
    assert view["egress"][2].startswith("otlp: refused (")
    assert any("telemetry" in line for line in view["privacy"])


def test_purge_requires_confirmation_and_attaches_utc(client: TestClient) -> None:
    run_job(client, "/api/runs", {"agent_ref": "research_assistant", "task": "Ferry time?"})
    assert post(client, "/api/settings/purge", {"scope": "runs"}).status_code == 422
    kept = post(
        client,
        "/api/settings/purge",
        {"confirm": "PURGE", "scope": "runs", "before": "2000-01-01T00:00:00"},
    )
    assert kept.status_code == 200, kept.text
    assert kept.json()["deleted"]["runs"] == 0
    purged = post(client, "/api/settings/purge", {"confirm": "PURGE", "scope": "runs"}).json()
    assert purged["deleted"]["runs"] == 1
    assert client.get("/api/runs").json()["total"] == 0


def test_purge_refuses_while_jobs_run(
    client: TestClient, services: Services, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def stall(*_args: object, **_kwargs: object) -> None:
        await asyncio.sleep(60)  # cancelled when the client (and its lifespan) closes

    monkeypatch.setattr(services, "run_case", stall)
    assert post(client, "/api/runs", {"agent_ref": "research_assistant", "task": "x"}).is_success
    response = post(client, "/api/settings/purge", {"confirm": "PURGE"})
    assert response.status_code == 409
    assert "running" in response.json()["detail"]


def test_incomplete_purge_is_a_conflict_with_its_result(
    client: TestClient, services: Services, monkeypatch: pytest.MonkeyPatch
) -> None:
    partial = PurgeResult(deleted={"runs": 2}, size_bytes_before=10, size_bytes_after=8)

    def incomplete(*_args: object) -> PurgeResult:
        raise PurgeIncomplete("a reader kept the WAL", partial)

    monkeypatch.setattr(services.repository, "purge", incomplete)
    response = post(client, "/api/settings/purge", {"confirm": "PURGE"})
    assert response.status_code == 409
    assert response.json() == {
        "detail": "a reader kept the WAL",
        "result": partial.model_dump(mode="json"),
    }


def test_remote_mode_requires_the_token_on_every_request(
    configured: Services, tmp_path: Path
) -> None:
    app = create_app(
        configured, web_dist=tmp_path / "none", api_token=TOKEN, allowed_hosts=["scope.example"]
    )
    with TestClient(app, base_url="http://scope.example:8765") as c:
        missing = c.get("/api/health")
        assert missing.status_code == 401
        assert missing.headers["www-authenticate"] == "Bearer"
        assert c.get("/api/runs", headers={"Authorization": "Bearer wrong"}).status_code == 401
        assert c.get("/api/runs", headers={"Authorization": TOKEN}).status_code == 401
        auth = {"Authorization": f"Bearer {TOKEN}"}
        assert c.get("/api/health", headers=auth).status_code == 200
        assert c.get("/api/nope", headers=auth).status_code == 404
        # The token does not lift the mutation guard.
        no_client_header = c.post(
            "/api/runs", json={"task": "x"}, headers={**auth, "Content-Type": "application/json"}
        )
        assert no_client_header.status_code == 403
        accepted = c.post(
            "/api/runs",
            json={"agent_ref": "research_assistant", "task": "x"},
            headers={**auth, **MUTATION},
        )
        assert accepted.status_code == 202
        assert c.get("/api/health", headers={**auth, "Host": "evil.example"}).status_code == 400


def test_errors_are_json_without_tracebacks(
    services: Services, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken() -> None:
        raise RuntimeError("database exploded at /secret/path.py line 12")

    monkeypatch.setattr(services.repository, "list_datasets", broken)
    app = create_app(services, web_dist=tmp_path / "none")
    with TestClient(app, base_url=BASE_URL, raise_server_exceptions=False) as c:
        response = c.get("/api/datasets")
        assert response.status_code == 500
        assert response.json() == {"detail": "internal error"}
        assert "Traceback" not in response.text
        assert response.headers["x-content-type-options"] == "nosniff"
        assert "default-src 'self'" in response.headers["content-security-policy"]

        invalid = c.post(
            "/api/runs",
            content=json.dumps({"task": "", "agent_ref": "sk-proj-abcdefghijklmnopqrstuvwx"}),
            headers=MUTATION,
        )
        assert invalid.status_code == 422
        body = invalid.json()
        assert body["detail"] == "the request is invalid"
        assert body["issues"][0]["path"] == "/body/task"
        assert "sk-proj" not in invalid.text  # submitted values are never echoed
        wrong_method = c.post("/api/health", headers=MUTATION)
        assert wrong_method.status_code == 404
        assert wrong_method.json() == {"detail": "not found"}


def test_settings_view_masks_credentials_embedded_in_urls(tmp_path: Path) -> None:
    # Egress refuses URLs with credentials, but the settings view must not echo them either.
    settings = Settings(
        data_dir=tmp_path / "urls",
        enable_ollama=True,
        ollama_url="http://admin:ollama-pass-0123@127.0.0.1:11434",
        otlp_endpoint="https://collector.example.com:4318/v1/traces?api_key=otlp-key-0123456",
    )
    svc = build_services(settings)
    try:
        with TestClient(create_app(svc, web_dist=tmp_path / "none"), base_url=BASE_URL) as c:
            response = c.get("/api/settings")
    finally:
        svc.repository.engine.dispose()
    assert response.status_code == 200
    assert "ollama-pass-0123" not in response.text
    assert "otlp-key-0123456" not in response.text
    view = response.json()
    assert view["effective"]["ollama_url"] == "http://***@127.0.0.1:11434"
    assert view["effective"]["otlp_endpoint"] == "https://collector.example.com:4318/v1/traces"
    # The OTLP endpoint is refused (not loopback, not allow-listed): nothing is exported.
    otlp = next(line for line in view["privacy"] if "OTLP" in line)
    assert "refused" in otlp
    assert not any("nothing is estimated" in line for line in view["privacy"])


def test_a_full_queue_answers_429(
    services: Services, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from agentscope.jobs import JobManager

    async def stall(*_args: object, **_kwargs: object) -> None:
        await asyncio.sleep(60)  # cancelled when the client (and its lifespan) closes

    monkeypatch.setattr(services, "run_case", stall)
    app = create_app(
        services,
        web_dist=tmp_path / "none",
        jobs_manager=JobManager(max_running=1, max_queued=1),
    )
    body = {"agent_ref": "research_assistant", "task": "Ferry crossing time?"}
    with TestClient(app, base_url=BASE_URL) as c:
        assert post(c, "/api/runs", body).status_code == 202  # running
        assert post(c, "/api/runs", body).status_code == 202  # queued
        full = post(c, "/api/runs", body)
        assert full.status_code == 429
        assert "try again" in full.json()["detail"]
        assert post(c, "/api/demo/seed").status_code == 429
