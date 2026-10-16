"""Evaluations, experiments, failure analysis, overview, providers and the demo seed."""

from __future__ import annotations

from typing import Any

from apiclient import post, run_job, wait_for_job
from fastapi.testclient import TestClient

from agentscope.datasets.packaged import load_builtin_dataset
from agentscope.services import Services

RESEARCH = {"name": "research-assistant", "version": "1"}
PLANNER = {"name": "task-planner", "version": "1"}


def evaluate(client: TestClient, agent_ref: str, **extra: Any) -> dict[str, Any]:
    job = run_job(
        client, "/api/evaluations", {"agent_ref": agent_ref, "dataset": RESEARCH, **extra}
    )
    assert job["kind"] == "evaluation"
    cases = len(load_builtin_dataset("research-assistant").cases)
    assert job["progress"] == job["total"] == cases
    suite: dict[str, Any] = client.get(f"/api/evaluations/{job['resource_id']}").json()
    return suite


def test_evaluation_lifecycle(client: TestClient) -> None:
    suite = evaluate(
        client,
        "research_assistant@v2",
        label="nightly",
        thresholds={"min_pass_rate": 0.5, "required_checks": ["policy_compliance"]},
    )
    assert suite["status"] == "completed"
    assert suite["label"] == "nightly"
    assert suite["error"] is None
    assert suite["gate"]["passed"] is True
    assert {r["name"] for r in suite["gate"]["rules"]} >= {
        "min_pass_rate",
        "required_check:policy_compliance",
    }
    assert suite["metrics"]["total"] == len(suite["cases"])

    listed = client.get("/api/evaluations").json()
    assert isinstance(listed, list)
    assert listed[0]["suite_id"] == suite["suite_id"]
    assert listed[0]["gate_passed"] is True
    assert client.get("/api/evaluations", params={"agent_ref": "nobody@v0"}).json() == []

    # Every case's trace was stored with the suite, its test case and its verdict.
    runs = client.get("/api/runs", params={"suite_id": suite["suite_id"], "limit": 200}).json()
    assert runs["total"] == len(suite["cases"])
    for case in suite["cases"]:
        detail = client.get(f"/api/runs/{case['run_id']}").json()
        assert detail["case"]["id"] == case["case_id"]
        assert detail["evaluation"]["passed"] == case["passed"]

    exported = client.get(f"/api/evaluations/{suite['suite_id']}/export")
    assert exported.headers["content-disposition"] == (
        f'attachment; filename="evaluation-{suite["suite_id"]}.json"'
    )
    assert exported.json()["suite_id"] == suite["suite_id"]


def test_evaluation_input_errors(client: TestClient) -> None:
    missing = post(
        client,
        "/api/evaluations",
        {"agent_ref": "research_assistant", "dataset": {"name": "nope", "version": "1"}},
    )
    assert missing.status_code == 404
    unknown = post(client, "/api/evaluations", {"agent_ref": "ghost@v1", "dataset": RESEARCH})
    assert unknown.status_code == 422
    wrong_agent = post(
        client, "/api/evaluations", {"agent_ref": "task_planner@v2", "dataset": RESEARCH}
    )
    assert wrong_agent.status_code == 422
    assert wrong_agent.json()["issues"]
    bad_body = post(client, "/api/evaluations", {"agent_ref": "research_assistant"})
    assert bad_body.status_code == 422
    assert client.get("/api/evaluations/not-a-uuid").status_code == 404
    assert (
        client.get("/api/evaluations/00000000-0000-0000-0000-000000000000/export").status_code
        == 404
    )
    assert client.get("/api/evaluations").json() == []


def test_experiment_run_mode_and_compare_mode(client: TestClient) -> None:
    job = run_job(
        client,
        "/api/experiments",
        {
            "name": "v1 vs v2",
            "dataset": RESEARCH,
            "a": {"agent_ref": "research_assistant@v1"},
            "b": {"agent_ref": "research_assistant@v2"},
        },
    )
    assert job["kind"] == "experiment"
    assert job["total"] == 2 * len(load_builtin_dataset("research-assistant").cases)
    assert job["progress"] == job["total"]
    comparison = client.get(f"/api/experiments/{job['resource_id']}").json()
    assert comparison["a"]["agent_ref"] == "research_assistant@v1"
    assert comparison["b"]["agent_ref"] == "research_assistant@v2"
    assert comparison["deterministic"] is True
    assert comparison["confounded"] is True

    compared = post(
        client,
        "/api/experiments",
        {
            "name": "again",
            "suite_a": comparison["a"]["suite_id"],
            "suite_b": comparison["b"]["suite_id"],
        },
    )
    assert compared.status_code == 201, compared.text
    assert compared.json()["cases"] == comparison["cases"]

    listed = client.get("/api/experiments").json()
    assert [e["name"] for e in listed] == ["again", "v1 vs v2"]
    assert client.get("/api/experiments/not-a-uuid").status_code == 404


def test_experiment_input_errors(client: TestClient, services: Services) -> None:
    both = post(
        client,
        "/api/experiments",
        {"name": "x", "suite_a": "a", "suite_b": "b", "dataset": RESEARCH},
    )
    assert both.status_code == 422
    assert post(client, "/api/experiments", {"name": "x"}).status_code == 422
    half = post(client, "/api/experiments", {"name": "x", "suite_a": "a"})
    assert half.status_code == 422
    missing = post(
        client,
        "/api/experiments",
        {"name": "x", "suite_a": "00000000-0000-0000-0000-000000000000", "suite_b": "nope"},
    )
    assert missing.status_code == 404
    no_b = post(
        client,
        "/api/experiments",
        {"name": "x", "dataset": RESEARCH, "a": {"agent_ref": "research_assistant@v1"}},
    )
    assert no_b.status_code == 422

    # A suite that has not finished cannot be compared.
    suite = evaluate(client, "research_assistant@v1")
    services.repository.fail_suite(suite["suite_id"], "cancelled")
    unfinished = post(
        client,
        "/api/experiments",
        {"name": "x", "suite_a": suite["suite_id"], "suite_b": suite["suite_id"]},
    )
    assert unfinished.status_code == 409


def test_failures_overview_and_providers(client: TestClient) -> None:
    v1 = evaluate(client, "research_assistant@v1")
    v2 = evaluate(client, "research_assistant@v2")

    failures = client.get("/api/failures").json()
    assert set(failures["suites_considered"]) == {v1["suite_id"], v2["suite_id"]}
    assert failures["buckets"]
    assert {row["agent_ref"] for row in failures["injection"]} == {
        "research_assistant@v1",
        "research_assistant@v2",
    }
    only_v1 = client.get("/api/failures", params={"agent_ref": "research_assistant@v1"}).json()
    assert only_v1["suites_considered"] == [v1["suite_id"]]
    chosen = client.get("/api/failures", params=[("suite_id", v2["suite_id"])]).json()
    assert chosen["suites_considered"] == [v2["suite_id"]]

    overview = client.get("/api/overview").json()
    assert overview["counts"]["suites"] == 2
    assert overview["counts"]["runs"] == v1["metrics"]["total"] + v2["metrics"]["total"]
    assert overview["counts"]["datasets"] >= 2
    assert {s["agent_ref"] for s in overview["pass_rate_series"]} == {
        "research_assistant@v1",
        "research_assistant@v2",
    }
    assert overview["latest_gate"] is None  # neither evaluation had thresholds
    assert [p["name"] for p in overview["providers"]] == ["fake", "ollama", "openai_compatible"]

    providers = client.get("/api/providers").json()
    assert providers[0] == {
        "name": "fake",
        "enabled": True,
        "deterministic": True,
        "reachable": True,
        "target": None,
        "models": ["scripted-careful", "scripted-compliant", "scripted-naive"],
        "detail": "Simulated model: demonstrates harness mechanics, not model behaviour.",
    }
    assert providers[1]["enabled"] is False  # Ollama is disabled in these tests: no probe


def test_demo_seed_job(client: TestClient) -> None:
    first = post(client, "/api/demo/seed")
    assert first.status_code == 202
    accepted = first.json()
    assert accepted["kind"] == "demo_seed"
    # A second request while the first is in progress joins it instead of seeding twice.
    second = post(client, "/api/demo/seed").json()
    status = wait_for_job(client, accepted["job_id"])
    assert status["state"] == "succeeded", status
    assert status["progress"] == status["total"] > 0
    if second["job_id"] != accepted["job_id"]:
        assert wait_for_job(client, second["job_id"])["state"] == "succeeded"
    suites = client.get("/api/evaluations").json()
    assert len(suites) == 5
    assert len(client.get("/api/experiments").json()) == 2
