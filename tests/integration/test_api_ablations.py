"""POST /api/ablations as a job, the ablation documents and list, and the evaluation list
that hides variant suites unless asked."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from apiclient import post, run_job, wait_for_job
from fastapi.testclient import TestClient

from agentscope.domain.datasets import Dataset
from agentscope.jobs import JobManager
from agentscope.services import Services

SMALL = {"name": "ablation-small", "version": "1"}
V1 = {"agent_ref": "research_assistant@v1"}
V2 = {"agent_ref": "research_assistant@v2"}


@pytest.fixture
def small(services: Services, small_research: Dataset) -> Dataset:
    services.import_dataset(small_research)
    return small_research


def jobs(client: TestClient) -> JobManager:
    manager: JobManager = client.app.state.jobs  # type: ignore[attr-defined]
    return manager


@pytest.mark.usefixtures("small")
def test_ablation_job_lifecycle(client: TestClient) -> None:
    response = post(client, "/api/ablations", {"dataset": SMALL, "a": V1, "b": V2})
    assert response.status_code == 202, response.text
    accepted = response.json()
    assert accepted["kind"] == "ablation"
    job = wait_for_job(client, accepted["job_id"])
    assert job["state"] == "succeeded", job
    # A, B, five dimensions both ways and ten pairs: 22 suites of 3 cases.
    assert job["progress"] == job["total"] == 22 * 3
    assert job["resource_id"] == accepted["resource_id"]

    ablation: dict[str, Any] = client.get(f"/api/ablations/{accepted['resource_id']}").json()
    assert ablation["ablation_id"] == accepted["resource_id"]
    assert ablation["name"] == "research_assistant@v1 vs research_assistant@v2"
    assert ablation["experiment_id"] is None
    assert (ablation["n"], ablation["deterministic"]) == (3, True)
    assert ablation["dimensions"] == ["graph", "isolation", "prompt", "retry", "tool_schema"]
    assert len(ablation["variants"]) == 10
    assert len(ablation["pairs"]) == 10
    together = {"+".join(p["dimensions"]): p["interaction_pp"] for p in ablation["pairs"]}
    assert together["isolation+prompt"] > 0

    listed = client.get("/api/ablations")
    assert listed.headers["X-Total-Count"] == "1"
    item = listed.json()[0]
    assert (item["ablation_id"], item["a_ref"], item["b_ref"]) == (
        ablation["ablation_id"],
        "research_assistant@v1",
        "research_assistant@v2",
    )
    assert item["total_effect_pp"] == ablation["total_effect_pp"]

    # A and B are ordinary evaluations; the 20 variant suites are listed only when asked.
    evaluations = client.get("/api/evaluations")
    assert evaluations.headers["X-Total-Count"] == "2"
    everything = client.get("/api/evaluations", params={"include_ablations": True, "limit": 200})
    assert everything.headers["X-Total-Count"] == "22"
    variant = ablation["variants"][0]["suite_id"]
    assert variant in {s["suite_id"] for s in everything.json()}
    assert client.get(f"/api/evaluations/{variant}").status_code == 200


@pytest.mark.usefixtures("small")
def test_a_stored_experiment_is_ablated_with_its_suites(client: TestClient) -> None:
    experiment = run_job(
        client, "/api/experiments", {"name": "small", "dataset": SMALL, "a": V1, "b": V2}
    )
    experiment_id = experiment["resource_id"]
    job = run_job(client, "/api/ablations", {"experiment_id": experiment_id, "name": "why"})
    assert job["kind"] == "ablation"
    assert job["total"] == 20 * 3  # A and B are reused
    ablation = client.get(f"/api/ablations/{job['resource_id']}").json()
    comparison = client.get(f"/api/experiments/{experiment_id}").json()
    assert (ablation["name"], ablation["experiment_id"]) == ("why", experiment_id)
    assert ablation["a"]["suite_id"] == comparison["a"]["suite_id"]
    assert ablation["b"]["suite_id"] == comparison["b"]["suite_id"]

    by_experiment = client.get("/api/ablations", params={"experiment_id": experiment_id})
    assert by_experiment.headers["X-Total-Count"] == "1"
    assert by_experiment.json()[0]["experiment_id"] == experiment_id
    other = client.get(
        "/api/ablations", params={"experiment_id": "00000000-0000-0000-0000-000000000000"}
    )
    assert (other.json(), other.headers["X-Total-Count"]) == ([], "0")


@pytest.mark.usefixtures("small")
def test_invalid_input_is_refused_before_a_job_starts(client: TestClient) -> None:
    unknown = "00000000-0000-0000-0000-000000000000"
    cases: list[tuple[dict[str, Any], int, str]] = [
        ({}, 422, "either experiment_id"),
        ({"experiment_id": unknown, "dataset": SMALL}, 422, "either experiment_id"),
        ({"dataset": SMALL, "a": V1}, 422, "dataset, a and b"),
        ({"dataset": SMALL, "a": V1, "b": V1}, 422, "nothing to ablate"),
        ({"dataset": SMALL, "a": V1, "b": {"agent_ref": "nobody@v1"}}, 422, "nobody"),
        ({"dataset": {"name": "missing", "version": "1"}, "a": V1, "b": V2}, 404, "missing"),
        ({"experiment_id": unknown}, 404, unknown),
        ({"experiment_id": "not-a-uuid"}, 404, "not-a-uuid"),
    ]
    for body, status, detail in cases:
        response = post(client, "/api/ablations", body)
        assert response.status_code == status, (body, response.text)
        assert detail in response.json()["detail"], body
    assert jobs(client).active() == []
    assert client.get("/api/ablations").headers["X-Total-Count"] == "0"


def test_an_experiment_on_an_unstored_dataset_is_404(
    client: TestClient, services: Services, small_research: Dataset
) -> None:
    """Its suites evaluated a dataset that was never imported: like an unknown dataset."""
    comparison = asyncio.run(
        services.run_experiment(
            "unstored",
            small_research,
            services.catalog.resolve(V1["agent_ref"]),
            services.catalog.resolve(V2["agent_ref"]),
        )
    )
    response = post(client, "/api/ablations", {"experiment_id": comparison.experiment_id})
    assert response.status_code == 404, response.text
    assert "which is not stored" in response.json()["detail"]
    assert jobs(client).active() == []


def test_ablation_documents_and_pages(client: TestClient) -> None:
    assert client.get("/api/ablations/00000000-0000-0000-0000-000000000000").status_code == 404
    assert client.get("/api/ablations/nope").status_code == 404
    page = client.get("/api/ablations", params={"limit": 1, "offset": 5})
    assert (page.status_code, page.json(), page.headers["X-Total-Count"]) == (200, [], "0")
    assert client.get("/api/ablations", params={"limit": 0}).status_code == 422
    assert "/api/ablations" in client.get("/api/openapi.json").json()["paths"]


@pytest.mark.usefixtures("small")
def test_unjudged_variants_fail_the_job_but_keep_the_ablation(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from agentscope.providers.base import ProviderError
    from agentscope.providers.fake import FakeModelProvider

    experiment = run_job(
        client, "/api/experiments", {"name": "small", "dataset": SMALL, "a": V1, "b": V2}
    )

    async def refuse(*_args: object, **_kwargs: object) -> None:
        raise ProviderError("connection refused")

    monkeypatch.setattr(FakeModelProvider, "generate", refuse)
    accepted = post(client, "/api/ablations", {"experiment_id": experiment["resource_id"]})
    job = wait_for_job(client, accepted.json()["job_id"])
    assert job["state"] == "failed"
    assert "could not be judged" in job["error"]
    ablation = client.get(f"/api/ablations/{job['resource_id']}").json()
    assert all(v["harness_errors"] == v["total"] == 3 for v in ablation["variants"])
    assert any("could not be judged" in warning for warning in ablation["warnings"])
