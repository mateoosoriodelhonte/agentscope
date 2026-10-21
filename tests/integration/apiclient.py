"""Helpers for API tests: the headers the dashboard sends, and job polling."""

from __future__ import annotations

import time
from typing import Any

import httpx
from fastapi.testclient import TestClient

MUTATION = {"Content-Type": "application/json", "X-AgentScope-Client": "1"}
BASE_URL = "http://127.0.0.1:8765"


def post(client: TestClient, path: str, body: Any = None) -> httpx.Response:
    return client.post(path, json={} if body is None else body, headers=MUTATION)


def wait_for_job(client: TestClient, job_id: str, timeout_s: float = 180) -> dict[str, Any]:
    """Poll GET /api/jobs/{id} until the job finishes, as the dashboard does. The deadline
    is generous: shared CI runners are several times slower than a laptop."""
    deadline = time.monotonic() + timeout_s
    while True:
        response = client.get(f"/api/jobs/{job_id}")
        assert response.status_code == 200, response.text
        status: dict[str, Any] = response.json()
        if status["state"] in ("succeeded", "failed"):
            return status
        assert time.monotonic() < deadline, f"job did not finish: {status}"
        time.sleep(0.02)


def run_job(client: TestClient, path: str, body: Any = None) -> dict[str, Any]:
    """POST a job-starting request, wait for it, and return the finished job (succeeded)."""
    response = post(client, path, body)
    assert response.status_code == 202, response.text
    accepted = response.json()
    status = wait_for_job(client, accepted["job_id"])
    assert status["state"] == "succeeded", status
    assert status["resource_id"] == accepted["resource_id"]
    return status
