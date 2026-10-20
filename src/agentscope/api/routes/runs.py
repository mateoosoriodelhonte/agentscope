from __future__ import annotations

import json
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response
from pydantic import ValidationError

from agentscope.api.deps import JobsDep, ServicesDep, accepted, download, submit
from agentscope.api.schemas import JobAccepted, RunCreate, RunDetail, RunList
from agentscope.domain.datasets import EvalCase
from agentscope.domain.traces import RunStatus
from agentscope.jobs import Job
from agentscope.runtime.runner import RunRequest

router = APIRouter(prefix="/api/runs", tags=["runs"])


@router.get("")
def list_runs(
    services: ServicesDep,
    agent: str | None = None,
    agent_version: str | None = None,
    status: RunStatus | None = None,
    has_faults: bool | None = None,
    has_blocked_calls: bool | None = None,
    verdict: Literal["passed", "failed", "none"] | None = None,
    case_id: str | None = None,
    suite_id: str | None = None,
    include_ablations: bool = False,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> RunList:
    items, total = services.repository.list_runs(
        agent=agent,
        agent_version=agent_version,
        status=status.value if status else None,
        has_faults=has_faults,
        has_blocked_calls=has_blocked_calls,
        verdict=verdict,
        case_id=case_id,
        suite_id=suite_id,
        include_ablations=include_ablations,
        limit=limit,
        offset=offset,
    )
    return RunList(items=items, total=total)


def _detail(services: ServicesDep, run_id: str) -> RunDetail:
    found = services.repository.get_run(run_id)
    if found is None:
        raise HTTPException(status_code=404, detail="run not found")
    trace, case, evaluation = found
    return RunDetail(trace=trace, case=case, evaluation=evaluation)


@router.get("/{run_id}")
def get_run(run_id: str, services: ServicesDep) -> RunDetail:
    return _detail(services, run_id)


@router.get("/{run_id}/export", response_class=Response)
def export_run(run_id: str, services: ServicesDep) -> Response:
    detail = _detail(services, run_id)
    body = json.dumps(detail.model_dump(mode="json"), indent=2, sort_keys=True)
    # The id was just found in storage, so it is a canonical UUID: a safe file name.
    return download(body.encode(), f"run-{detail.trace.run_id}.json")


@router.post("", status_code=202)
async def create_run(body: RunCreate, services: ServicesDep, jobs: JobsDep) -> JobAccepted:
    """Start a run in the background; poll the job, then GET /api/runs/{resource_id}."""
    try:
        config = services.resolve_agent(body.agent_ref, body.config)
    except (ValueError, LookupError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    try:
        case = (
            EvalCase(
                id="adhoc",
                task=body.task,
                expected=body.expected,
                faults=body.faults,
                model_faults=body.model_faults,
            )
            if body.expected is not None
            else None
        )
    except ValidationError as exc:
        raise HTTPException(
            status_code=422, detail="the run cannot be evaluated as a test case"
        ) from exc
    request = RunRequest(
        config=config, task=body.task, faults=body.faults, model_faults=body.model_faults
    )

    async def work(_job: Job) -> None:
        await services.run_case(request, case)

    return accepted(submit(jobs, "run", request.run_id, work))
