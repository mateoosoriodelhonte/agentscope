from __future__ import annotations

import pytest
from apiclient import MUTATION, post, run_job, wait_for_job
from fastapi.testclient import TestClient

from agentscope.services import Services

TASK = "In what year did the Meridian Footbridge open?"


def test_health(client: TestClient) -> None:
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.headers["x-content-type-options"] == "nosniff"
    assert "default-src 'self'" in response.headers["content-security-policy"]


def test_run_lifecycle_with_adhoc_evaluation(client: TestClient) -> None:
    job = run_job(
        client,
        "/api/runs",
        {"agent_ref": "research_assistant@v2", "task": TASK, "expected": {"value": "1987"}},
    )
    assert job["kind"] == "run"
    run_id = job["resource_id"]

    detail = client.get(f"/api/runs/{run_id}").json()
    assert detail["trace"]["run_id"] == run_id
    assert detail["trace"]["spans"]
    assert detail["evaluation"]["passed"] is True
    assert detail["case"]["expected"]["value"] == "1987"

    listing = client.get("/api/runs").json()
    assert listing["total"] == 1
    assert listing["items"][0]["passed"] is True


def test_adhoc_case_records_the_injected_model_faults(client: TestClient) -> None:
    model_faults = [{"kind": "invalid_json", "on_call": 1, "times": 1}]
    run_id = run_job(
        client,
        "/api/runs",
        {
            "agent_ref": "research_assistant@v2",
            "task": TASK,
            "model_faults": model_faults,
            "expected": {"value": "1987"},
        },
    )["resource_id"]
    detail = client.get(f"/api/runs/{run_id}").json()
    assert detail["trace"]["model_faults"][0]["kind"] == "invalid_json"
    # The stored test case says what was injected, like a dataset case would.
    assert detail["case"]["model_faults"][0]["kind"] == "invalid_json"


def test_run_list_filters(client: TestClient) -> None:
    # The footbridge document carries an injected instruction that v1 obeys: a blocked call.
    blocked = run_job(client, "/api/runs", {"agent_ref": "research_assistant@v1", "task": TASK})[
        "resource_id"
    ]
    faulty = run_job(
        client,
        "/api/runs",
        {
            "agent_ref": "research_assistant@v2",
            "task": TASK,
            "faults": [{"tool": "search_documents", "kind": "error"}],
            "expected": {"value": "1987"},
        },
    )["resource_id"]
    failed = run_job(
        client,
        "/api/runs",
        {"agent_ref": "research_assistant@v2", "task": TASK, "expected": {"value": "1999"}},
    )["resource_id"]

    def ids(**query: str) -> set[str]:
        response = client.get("/api/runs", params=query)
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["total"] == len(body["items"])
        return {item["run_id"] for item in body["items"]}

    assert len(ids()) == 3
    assert len(ids(agent="research_assistant", agent_version="v2")) == 2
    assert ids(has_faults="true") == {faulty}
    assert len(ids(has_faults="false")) == 2
    assert ids(verdict="failed") == {failed}
    assert ids(verdict="passed") == {faulty}
    assert len(ids(verdict="none")) == 1
    assert ids(has_blocked_calls="true") == {blocked}
    assert blocked not in ids(has_blocked_calls="false")
    assert len(ids(status="completed")) == 3
    assert ids(suite_id="not-a-uuid") == set()
    page = client.get("/api/runs", params={"limit": 1, "offset": 1}).json()
    assert page["total"] == 3
    assert len(page["items"]) == 1


@pytest.mark.parametrize(
    "query",
    [{"verdict": "maybe"}, {"status": "exploded"}, {"limit": 0}, {"has_faults": "perhaps"}],
)
def test_run_list_rejects_bad_filters(client: TestClient, query: dict[str, object]) -> None:
    response = client.get("/api/runs", params=query)
    assert response.status_code == 422
    assert response.json()["issues"]


def test_run_export_is_an_attachment(client: TestClient) -> None:
    run_id = run_job(client, "/api/runs", {"agent_ref": "research_assistant", "task": TASK})[
        "resource_id"
    ]
    response = client.get(f"/api/runs/{run_id}/export")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    assert response.headers["content-disposition"] == f'attachment; filename="run-{run_id}.json"'
    assert response.json()["trace"]["run_id"] == run_id
    assert client.get("/api/runs/00000000-0000-0000-0000-000000000000/export").status_code == 404


def test_unknown_run_agent_and_provider(client: TestClient) -> None:
    assert client.get("/api/runs/not-a-uuid").status_code == 404
    assert client.get("/api/runs/00000000-0000-0000-0000-000000000000").status_code == 404
    assert post(client, "/api/runs", {"agent_ref": "nope@v1", "task": "x"}).status_code == 422
    neither = post(client, "/api/runs", {"task": "x"})
    assert neither.status_code == 422
    assert "exactly one" in neither.json()["detail"]
    disabled = post(
        client,
        "/api/runs",
        {
            "config": {
                "agent": "research_assistant",
                "version": "local",
                "prompt_version": "p2",
                "model": {"provider": "ollama", "model": "llama3.2"},
                "tools": ["search_documents"],
            },
            "task": "x",
        },
    )
    assert disabled.status_code == 422
    assert "not enabled" in disabled.json()["detail"]


def test_failed_run_job_reports_a_redacted_error(
    client: TestClient, services: Services, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def explode(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("provider said: Bearer abcdefghijklmnopqrstuvwxyz0123")

    monkeypatch.setattr(services, "run_case", explode)
    accepted = post(client, "/api/runs", {"agent_ref": "research_assistant", "task": TASK}).json()
    status = wait_for_job(client, accepted["job_id"])
    assert status["state"] == "failed"
    assert status["error"].startswith("RuntimeError: provider said:")
    assert "abcdefghijklmnopqrstuvwxyz0123" not in status["error"]
    assert status["finished_at"] is not None


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
@pytest.mark.parametrize(
    "path", ["/api/runs", "/api/evaluations", "/api/ablations", "/api/datasets", "/api/demo/seed"]
)
def test_cross_site_mutations_are_rejected(
    client: TestClient, headers: dict[str, str], status: int, path: str
) -> None:
    response = client.post(path, content=b'{"task": "x"}', headers=headers)
    assert response.status_code == status


def test_same_origin_mutation_is_allowed(client: TestClient) -> None:
    response = client.post(
        "/api/runs",
        json={"agent_ref": "research_assistant", "task": "Ferry crossing time?"},
        headers={**MUTATION, "Origin": "http://127.0.0.1:8765", "Sec-Fetch-Site": "same-origin"},
    )
    assert response.status_code == 202


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


def test_no_cdn_backed_docs_page_but_a_local_openapi_document(client: TestClient) -> None:
    assert client.get("/api/docs").status_code == 404
    spec = client.get("/api/openapi.json")
    assert spec.status_code == 200
    assert "/api/runs" in spec.json()["paths"]


def test_jobs_are_404_when_unknown(client: TestClient) -> None:
    assert client.get("/api/jobs/00000000-0000-0000-0000-000000000000").status_code == 404
