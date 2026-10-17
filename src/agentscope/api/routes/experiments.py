from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse

from agentscope.api.deps import JobsDep, ServicesDep, accepted, submit
from agentscope.api.routes.evaluations import suite_inputs
from agentscope.api.schemas import ExperimentCreate, ExperimentListItem, JobAccepted
from agentscope.domain.experiments import Comparison
from agentscope.jobs import Job
from agentscope.services import UnknownSuiteError

router = APIRouter(prefix="/api/experiments", tags=["experiments"])


@router.get("")
def list_experiments(
    services: ServicesDep,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[ExperimentListItem]:
    items, _ = services.repository.list_experiments(limit=limit, offset=offset)
    return items


@router.post(
    "",
    status_code=202,
    response_model=JobAccepted,
    responses={201: {"model": Comparison, "description": "two finished suites compared"}},
)
async def create_experiment(
    body: ExperimentCreate, services: ServicesDep, jobs: JobsDep
) -> JSONResponse:
    """`suite_a` + `suite_b`: compare two finished evaluations now (201, the Comparison).
    `dataset` + `a` + `b`: evaluate both sides in the background (202, a job)."""
    compare_mode = body.suite_a is not None or body.suite_b is not None
    run_mode = body.dataset is not None or body.a is not None or body.b is not None
    if compare_mode == run_mode:
        raise HTTPException(
            status_code=422,
            detail="provide either suite_a and suite_b, or dataset, a and b",
        )
    if compare_mode:
        if body.suite_a is None or body.suite_b is None:
            raise HTTPException(status_code=422, detail="provide both suite_a and suite_b")
        try:
            comparison = await run_in_threadpool(
                services.compare_suites, body.name, body.suite_a, body.suite_b
            )
        except UnknownSuiteError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return JSONResponse(comparison.model_dump(mode="json"), status_code=201)

    if body.dataset is None or body.a is None or body.b is None:
        raise HTTPException(status_code=422, detail="provide dataset, a and b")
    dataset, a_config = await run_in_threadpool(
        suite_inputs, services, body.dataset, body.a.agent_ref, body.a.config
    )
    _, b_config = await run_in_threadpool(
        suite_inputs, services, body.dataset, body.b.agent_ref, body.b.config
    )
    experiment_id = str(uuid.uuid4())

    async def work(job: Job) -> None:
        await services.run_experiment(
            body.name,
            dataset,
            a_config,
            b_config,
            experiment_id=experiment_id,
            progress=job.report,
        )

    job = submit(jobs, "experiment", experiment_id, work, total=2 * len(dataset.cases))
    return JSONResponse(accepted(job).model_dump(mode="json"), status_code=202)


@router.get("/{experiment_id}")
def get_experiment(experiment_id: str, services: ServicesDep) -> Comparison:
    comparison = services.repository.get_experiment(experiment_id)
    if comparison is None:
        raise HTTPException(status_code=404, detail="experiment not found")
    return comparison
