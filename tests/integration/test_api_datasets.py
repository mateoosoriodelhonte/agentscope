from __future__ import annotations

import json
from typing import Any

from apiclient import MUTATION, post
from fastapi.testclient import TestClient

from agentscope.datasets.loader import load_dataset_bytes
from agentscope.datasets.packaged import builtin_datasets

TASK = "In what year did the Meridian Footbridge open?"


def document(version: str = "1", **case: Any) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "name": "smoke",
        "version": version,
        "description": "API test dataset",
        "agent": "research_assistant",
        "cases": [{"id": "footbridge", "task": TASK, "expected": {"value": "1987"}, **case}],
    }


def test_builtin_datasets_are_registered_at_startup(client: TestClient) -> None:
    listed = client.get("/api/datasets").json()
    assert isinstance(listed, list)
    names = {(d["name"], d["version"]) for d in listed}
    assert {(d.name, d.version) for d in builtin_datasets()} <= names


def test_import_get_and_export(client: TestClient) -> None:
    created = post(client, "/api/datasets", document())
    assert created.status_code == 201, created.text
    summary = created.json()
    assert summary["name"] == "smoke"
    assert summary["case_count"] == 1

    again = post(client, "/api/datasets", document())  # identical content: idempotent
    assert again.status_code == 201
    assert again.json()["content_hash"] == summary["content_hash"]

    detail = client.get("/api/datasets/smoke/1").json()
    assert detail["summary"]["content_hash"] == summary["content_hash"]
    assert detail["dataset"]["cases"][0]["id"] == "footbridge"

    exported = client.get("/api/datasets/smoke/1/export")
    assert exported.status_code == 200
    assert exported.headers["content-disposition"] == (
        'attachment; filename="smoke@1.dataset.json"'
    )
    assert load_dataset_bytes(exported.content).content_hash() == summary["content_hash"]


def test_import_conflict_and_unknown_dataset(client: TestClient) -> None:
    assert post(client, "/api/datasets", document()).status_code == 201
    changed = document()
    changed["description"] = "different content under the same version"
    conflict = post(client, "/api/datasets", changed)
    assert conflict.status_code == 409
    assert "already exists" in conflict.json()["detail"]
    assert client.get("/api/datasets/smoke/9").status_code == 404
    assert client.get("/api/datasets/smoke/9/export").status_code == 404


def test_import_rejects_invalid_documents_with_located_issues(client: TestClient) -> None:
    raw = b'{"schema_version": 1, "name": "a", "name": "b", "cases": []}'
    duplicate = client.post("/api/datasets", content=raw, headers=MUTATION)
    assert duplicate.status_code == 422
    body = duplicate.json()
    assert body["detail"] == "the dataset is invalid"
    assert {"path": "/name", "message": "duplicate key", "severity": "error"} in body["issues"]

    unknown_tool = document(expected_tool_calls=[{"tool": "teleport"}])
    linted = post(client, "/api/datasets", unknown_tool)
    assert linted.status_code == 422
    assert any("teleport" in issue["message"] for issue in linted.json()["issues"])

    not_json = client.post("/api/datasets", content=b"{nope", headers=MUTATION)
    assert not_json.status_code == 422
    assert client.get("/api/datasets").json()  # nothing broke


def test_secret_shaped_values_are_refused_not_rewritten(client: TestClient) -> None:
    leaky = document(forbidden_output_substrings=["sk-proj-abcdefghijklmnopqrstuvwx"])
    refused = post(client, "/api/datasets", leaky)
    assert refused.status_code == 422
    issues = refused.json()["issues"]
    assert issues[0]["path"] == "/cases/0/forbidden_output_substrings/0"
    assert "synthetic canaries" in issues[0]["message"]
    assert client.get("/api/datasets/smoke/1").status_code == 404

    validation = post(client, "/api/datasets/validate", leaky).json()
    assert validation["valid"] is False
    assert validation["issues"][0]["path"] == "/cases/0/forbidden_output_substrings/0"


def test_validate_reports_without_importing(client: TestClient) -> None:
    good = post(client, "/api/datasets/validate", document())
    assert good.status_code == 200
    report = good.json()
    assert report["valid"] is True
    assert report["case_count"] == 1
    assert report["content_hash"].startswith("sha256:")

    bad = client.post(
        "/api/datasets/validate", content=json.dumps({"name": "x"}).encode(), headers=MUTATION
    ).json()
    assert bad["valid"] is False
    assert bad["content_hash"] is None
    assert any(issue["path"] == "/cases" for issue in bad["issues"])
    assert client.get("/api/datasets/smoke/1").status_code == 404
