from __future__ import annotations

import json
import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import Response

from agentscope.api.deps import (
    InvalidInput,
    JobsDep,
    ServicesDep,
    accepted,
    download,
    submit,
)
from agentscope.api.schemas import DatasetRef, EvaluationCreate, JobAccepted, SuiteListItem
from agentscope.datasets.issues import DatasetError
from agentscope.datasets.packaged import UnknownDatasetError
from agentscope.domain.agents import AgentConfig
from agentscope.domain.datasets import Dataset
from agentscope.domain.evaluation import SuiteResult
from agentscope.jobs import Job
from agentscope.services import Services, ensure_suite_judged

# Lists stay bare JSON arrays; the number of matching items (beyond this page) is a header.
TOTAL_COUNT = "X-Total-Count"

router = APIRouter(prefix="/api/evaluations", tags=["evaluations"])


def suite_inputs(
    services: Services, ref: DatasetRef, agent_ref: str | None, config: AgentConfig | None
) -> tuple[Dataset, AgentConfig]:
    """Resolve and check what a suite needs before a job is accepted: 404 for an unknown
    dataset, 422 for an unusable agent or a dataset that does not fit it."""
    try:
        dataset = services.stored_dataset(ref.name, ref.version)
    except UnknownDatasetError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    try:
        resolved = services.resolve_agent(agent_ref, config)
        services.check_suite_inputs(dataset, resolved)
    except DatasetError as exc:
        raise InvalidInput(
            f"dataset {ref.name}@{ref.version} cannot be evaluated with this agent", exc.issues
        ) from exc
    except (ValueError, LookupError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return dataset, resolved


@router.get("")
def list_evaluations(
    services: ServicesDep,
    response: Response,
    agent_ref: str | None = None,
    dataset: str | None = None,
    status: Literal["running", "completed", "error"] | None = None,
    include_ablations: bool = False,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[SuiteListItem]:
    """Newest first, one page at a time: X-Total-Count says how many match in all. The
    variant suites of ablations are listed only with `include_ablations`."""
    items, total = services.repository.list_suites(
        agent_ref=agent_ref,
        dataset_name=dataset,
        status=status,
        include_ablations=include_ablations,
        limit=limit,
        offset=offset,
    )
    response.headers[TOTAL_COUNT] = str(total)
    return items


@router.post("", status_code=202)
async def create_evaluation(
    body: EvaluationCreate, services: ServicesDep, jobs: JobsDep
) -> JobAccepted:
    """Start a suite in the background; poll the job, then GET /api/evaluations/{resource_id}."""
    # Loading and linting a stored dataset is blocking work: keep it off the event loop.
    dataset, config = await run_in_threadpool(
        suite_inputs, services, body.dataset, body.agent_ref, body.config
    )
    suite_id = str(uuid.uuid4())

    async def work(job: Job) -> None:
        result = await services.run_suite(
            dataset,
            config,
            label=body.label,
            thresholds=body.thresholds,
            suite_id=suite_id,
            progress=job.report,
        )
        # Cases the harness or the provider broke make the job fail; the suite stays stored.
        ensure_suite_judged(result)

    return accepted(submit(jobs, "evaluation", suite_id, work, total=len(dataset.cases)))


def _suite(services: Services, suite_id: str) -> SuiteResult:
    suite = services.repository.get_suite(suite_id)
    if suite is None:
        raise HTTPException(status_code=404, detail="evaluation not found")
    return suite


@router.get("/{suite_id}")
def get_evaluation(suite_id: str, services: ServicesDep) -> SuiteResult:
    return _suite(services, suite_id)


@router.get("/{suite_id}/export", response_class=Response)
def export_evaluation(suite_id: str, services: ServicesDep) -> Response:
    suite = _suite(services, suite_id)
    body = json.dumps(suite.model_dump(mode="json"), indent=2, sort_keys=True)
    return download(body.encode(), f"evaluation-{suite.suite_id}.json")
