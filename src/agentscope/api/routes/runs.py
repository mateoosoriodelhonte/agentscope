from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query

from agentscope.agents.base import UnknownAgentError
from agentscope.api.deps import get_services
from agentscope.api.schemas import RunCreate, RunDetail, RunList
from agentscope.domain.datasets import EvalCase
from agentscope.runtime.runner import RunRequest
from agentscope.services import Services

router = APIRouter(prefix="/api/runs", tags=["runs"])
ServicesDep = Annotated[Services, Depends(get_services)]


@router.get("")
def list_runs(
    services: ServicesDep,
    agent: str | None = None,
    status: str | None = None,
    case_id: str | None = None,
    suite_id: str | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> RunList:
    items, total = services.repository.list_runs(
        agent=agent, status=status, case_id=case_id, suite_id=suite_id, limit=limit, offset=offset
    )
    return RunList(items=items, total=total)


@router.get("/{run_id}")
def get_run(run_id: str, services: ServicesDep) -> RunDetail:
    found = services.repository.get_run(run_id)
    if found is None:
        raise HTTPException(status_code=404, detail="run not found")
    trace, case, evaluation = found
    return RunDetail(trace=trace, case=case, evaluation=evaluation)


@router.post("", status_code=201)
async def create_run(body: RunCreate, services: ServicesDep) -> RunDetail:
    if (body.agent_ref is None) == (body.config is None):
        raise HTTPException(status_code=422, detail="provide exactly one of agent_ref or config")
    try:
        config = body.config or services.catalog.resolve(body.agent_ref or "")
        services.catalog.validate(config)
    except UnknownAgentError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    case = (
        EvalCase(id="adhoc", task=body.task, expected=body.expected, faults=body.faults)
        if body.expected is not None
        else None
    )
    request = RunRequest(
        config=config, task=body.task, faults=body.faults, model_faults=body.model_faults
    )
    try:
        trace, evaluation = await services.run_case(request, case)
    except LookupError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return RunDetail(trace=trace, case=case, evaluation=evaluation)
