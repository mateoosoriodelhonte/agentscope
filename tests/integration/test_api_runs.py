from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agentscope.api.app import create_app
from agentscope.services import Services

MUTATION = {"Content-Type": "application/json", "X-AgentScope-Client": "1"}


@pytest.fixture
def client(services: Services, tmp_path: Path) -> Iterator[TestClient]:
    with TestClient(
        create_app(services, web_dist=tmp_path / "no-dist"), base_url="http://127.0.0.1:8765"
    ) as c:
        yield c


def test_health(client: TestClient) -> None:
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.headers["x-content-type-options"] == "nosniff"
    assert "default-src 'self'" in response.headers["content-security-policy"]


def test_run_lifecycle_with_adhoc_evaluation(client: TestClient) -> None:
    created = client.post(
        "/api/runs",
        json={
            "agent_ref": "research_assistant@v2",
            "task": "In what year did the Meridian Footbridge open?",
            "expected": {"value": "1987"},
        },
        headers=MUTATION,
    )
    assert created.status_code == 201, created.text
    detail = created.json()
    run_id = detail["trace"]["run_id"]
    assert detail["evaluation"]["passed"] is True

    listing = client.get("/api/runs").json()
    assert listing["total"] == 1
    assert listing["items"][0]["passed"] is True

    fetched = client.get(f"/api/runs/{run_id}").json()
    assert fetched["trace"]["trace_id"] == detail["trace"]["trace_id"]
    assert len(fetched["trace"]["spans"]) == len(detail["trace"]["spans"])
    assert fetched["evaluation"]["checks"][0]["name"] == "answer_correctness"


def test_unknown_run_and_agent(client: TestClient) -> None:
    assert client.get("/api/runs/not-a-uuid").status_code == 404
    bad = client.post("/api/runs", json={"agent_ref": "nope@v1", "task": "x"}, headers=MUTATION)
    assert bad.status_code == 422


@pytest.mark.parametrize(
    ("headers", "status"),
    [
        ({"Content-Type": "text/plain", "X-AgentScope-Client": "1"}, 403),  # simple CORS request
        ({"Content-Type": "application/json"}, 403),  # missing client header
        ({**MUTATION, "Origin": "https://evil.example"}, 403),
        ({**MUTATION, "Origin": "null"}, 403),
        ({**MUTATION, "Sec-Fetch-Site": "cross-site"}, 403),
    ],
)
def test_cross_site_mutations_are_rejected(
    client: TestClient, headers: dict[str, str], status: int
) -> None:
    response = client.post(
        "/api/runs", content=b'{"agent_ref": "research_assistant", "task": "x"}', headers=headers
    )
    assert response.status_code == status


def test_same_origin_mutation_is_allowed(client: TestClient) -> None:
    response = client.post(
        "/api/runs",
        json={"agent_ref": "research_assistant", "task": "Ferry crossing time?"},
        headers={**MUTATION, "Origin": "http://127.0.0.1:8765", "Sec-Fetch-Site": "same-origin"},
    )
    assert response.status_code == 201


def test_dns_rebinding_host_is_rejected(client: TestClient) -> None:
    assert client.get("/api/health", headers={"Host": "attacker.example:8765"}).status_code == 400
    assert client.get("/api/health", headers={"Host": "[::1]:8765"}).status_code == 200
    assert client.get("/api/health", headers={"Host": "localhost"}).status_code == 200


def test_oversized_body_is_rejected(client: TestClient) -> None:
    big = b'{"task": "' + b"a" * (300 * 1024) + b'"}'
    assert client.post("/api/runs", content=big, headers=MUTATION).status_code == 413


def test_unknown_api_route_is_json_404(client: TestClient) -> None:
    response = client.get("/api/nope")
    assert response.status_code == 404
    assert response.json() == {"detail": "not found"}
