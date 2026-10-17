"""The container image's entry points.

`agentscope serve --container` binds every interface of the container, needs no API token,
and keeps the loopback Host check. A port published as `127.0.0.1:8765:8765` delivers
requests whose Host header is `127.0.0.1:8765`, so the dashboard works from the host machine.
A request that reaches the container any other way (another container on the network, a
port published on a LAN address, a DNS-rebinding page) carries another Host and is refused.

The CI docker job also evaluates the packaged dataset inside the image; the last tests pin
that command's gate inputs.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from typer.testing import CliRunner, Result

from agentscope.cli.main import app
from agentscope.config import get_settings

cli = CliRunner()
TOKEN = "a-long-remote-token-0123456789"


def invoke(*args: str) -> Result:
    return cli.invoke(app, list(args))


class FakeUvicorn:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def run(self, app: Any, **kwargs: Any) -> None:
        self.calls.append({"app": app, **kwargs})


@pytest.fixture
def uvicorn(monkeypatch: pytest.MonkeyPatch) -> FakeUvicorn:
    import uvicorn as real

    fake = FakeUvicorn()
    monkeypatch.setattr(real, "run", fake.run)
    return fake


@pytest.fixture
def served(uvicorn: FakeUvicorn) -> Iterator[TestClient]:
    """The app `serve --container` would hand to uvicorn, behind a test client."""
    result = invoke("serve", "--container")
    assert result.exit_code == 0, result.output
    built: FastAPI = uvicorn.calls[0]["app"]
    with TestClient(built, base_url="http://127.0.0.1:8765") as client:
        yield client


def test_container_mode_binds_every_interface_without_a_token(uvicorn: FakeUvicorn) -> None:
    result = invoke("serve", "--container")
    assert result.exit_code == 0, result.output
    [call] = uvicorn.calls
    assert call["host"] == "0.0.0.0"
    assert call["port"] == 8765
    # The warning names the only safe way to publish the port.
    assert "container mode" in result.stderr
    assert "127.0.0.1:8765:8765" in result.stderr
    assert "remote mode" not in result.stderr


def test_container_mode_from_the_environment(
    uvicorn: FakeUvicorn, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AGENTSCOPE_CONTAINER", "1")
    result = invoke("serve", "--port", "9000")
    assert result.exit_code == 0, result.output
    assert (uvicorn.calls[0]["host"], uvicorn.calls[0]["port"]) == ("0.0.0.0", 9000)
    assert "127.0.0.1:9000:9000" in result.stderr


def test_an_explicit_host_wins_in_container_mode(uvicorn: FakeUvicorn) -> None:
    result = invoke("serve", "--container", "--host", "::")
    assert result.exit_code == 0, result.output
    assert uvicorn.calls[0]["host"] == "::"


def test_without_container_mode_a_wildcard_bind_is_still_refused(uvicorn: FakeUvicorn) -> None:
    result = invoke("serve", "--host", "0.0.0.0")
    assert result.exit_code == 2
    assert "--allow-remote" in result.output
    assert not uvicorn.calls


@pytest.mark.parametrize(
    "host",
    ["127.0.0.1:8765", "localhost:8765", "[::1]:8765", "127.0.0.1:9000"],
)
def test_loopback_host_headers_are_served_without_a_token(served: TestClient, host: str) -> None:
    response = served.get("/api/health", headers={"host": host})
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


@pytest.mark.parametrize(
    "host",
    ["172.17.0.2:8765", "agentscope:8765", "192.168.1.20", "attacker.example", "0.0.0.0:8765"],
)
def test_other_host_headers_are_refused(served: TestClient, host: str) -> None:
    for path in ("/api/health", "/api/runs", "/overview"):
        response = served.get(path, headers={"host": host})
        assert response.status_code == 400, (host, path)
        assert response.json() == {"detail": "invalid host header"}


def test_mutations_keep_the_client_header_and_origin_checks(served: TestClient) -> None:
    body = {"agent_ref": "research_assistant@v2", "task": "In what year did the bridge open?"}
    refused = served.post("/api/runs", json=body)
    assert refused.status_code == 403
    cross_origin = served.post(
        "/api/runs",
        json=body,
        headers={"x-agentscope-client": "1", "origin": "http://attacker.example"},
    )
    assert cross_origin.status_code == 403


def test_container_mode_refuses_extra_host_names_without_a_token(uvicorn: FakeUvicorn) -> None:
    result = invoke("serve", "--container", "--allowed-host", "agentscope.internal")
    assert result.exit_code == 2
    assert "--allow-remote" in result.output
    assert not uvicorn.calls


def test_container_mode_with_allow_remote_still_requires_the_token(
    uvicorn: FakeUvicorn, monkeypatch: pytest.MonkeyPatch
) -> None:
    refused = invoke("serve", "--container", "--allow-remote")
    assert refused.exit_code == 2
    assert "AGENTSCOPE_API_TOKEN" in refused.output
    assert not uvicorn.calls

    monkeypatch.setenv("AGENTSCOPE_API_TOKEN", TOKEN)
    get_settings.cache_clear()
    started = invoke(
        "serve", "--container", "--allow-remote", "--allowed-host", "agentscope.internal"
    )
    assert started.exit_code == 0, started.output
    assert TOKEN not in started.output
    assert "remote mode" in started.stderr
    assert "container mode" not in started.stderr
    [call] = uvicorn.calls
    assert call["host"] == "0.0.0.0"
    with TestClient(call["app"], base_url="http://agentscope.internal:8765") as client:
        assert client.get("/api/health").status_code == 401
        authorized = client.get("/api/health", headers={"authorization": f"Bearer {TOKEN}"})
        assert authorized.status_code == 200


# --- the smoke evaluation the CI docker job runs inside the image ------------------------------

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
SMOKE = ("eval", "--dataset", "research-assistant", "--agent", "research_assistant@v2", "--check")


def test_the_image_smoke_evaluation_passes_the_ci_gate() -> None:
    """The packaged research-assistant dataset under the quality-gate thresholds and v2's
    baseline (the fixture dataset is a byte-for-byte copy, so the baseline applies). The
    docker job mounts tests/fixtures read-only at /fixtures and runs exactly this."""
    result = invoke(
        *SMOKE,
        "--thresholds",
        str(FIXTURES / "gate.json"),
        "--baseline",
        str(FIXTURES / "research_assistant@v2.baseline.json"),
        "--no-save",
        "--json",
    )
    assert result.exit_code == 0, result.output
    gate = json.loads(result.stdout)["gate"]
    assert gate["passed"] is True
    assert gate["baseline_used"] is True


def test_without_gate_inputs_check_demands_every_case() -> None:
    # Why the docker job passes the thresholds: v2 deliberately fails 8 of the 29 cases, and
    # --check with no thresholds requires all of them to pass.
    result = invoke(*SMOKE, "--no-save", "--json")
    assert result.exit_code == 1
    rules = {r["name"]: r for r in json.loads(result.stdout)["gate"]["rules"]}
    assert rules["min_pass_rate"]["actual"] == "21/29"
