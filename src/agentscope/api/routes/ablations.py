from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import Response

from agentscope.api.deps import InvalidInput, JobsDep, ServicesDep, accepted, submit
from agentscope.api.routes.evaluations import TOTAL_COUNT, suite_inputs
from agentscope.api.schemas import AblationCreate, JobAccepted
from agentscope.datasets.issues import DatasetError
from agentscope.domain.ablations import Ablation, AblationListItem
from agentscope.jobs import Job
from agentscope.services import (
    AblationPlan,
    Services,
    UnknownExperimentError,
    ensure_ablation_judged,
)

router = APIRouter(prefix="/api/ablations", tags=["ablations"])


@router.get("")
def list_ablations(
    services: ServicesDep,
    response: Response,
    experiment_id: str | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[AblationListItem]:
    """Newest first, one page at a time: X-Total-Count says how many match in all."""
    items, total = services.repository.list_ablations(
        experiment_id=experiment_id, limit=limit, offset=offset
    )
    response.headers[TOTAL_COUNT] = str(total)
    return items


def _plan(services: Services, body: AblationCreate) -> AblationPlan:
    """Everything that can be refused, before a job is accepted: 404 for an unknown
    experiment or dataset, 422 for anything that cannot be ablated."""
    by_experiment = body.experiment_id is not None
    by_agents = body.dataset is not None or body.a is not None or body.b is not None
    if by_experiment == by_agents:
        raise HTTPException(
            status_code=422, detail="provide either experiment_id, or dataset, a and b"
        )
    try:
        if body.experiment_id is not None:
            return services.plan_experiment_ablation(body.experiment_id, name=body.name)
        if body.dataset is None or body.a is None or body.b is None:
            raise HTTPException(status_code=422, detail="provide dataset, a and b")
        dataset, a_config = suite_inputs(services, body.dataset, body.a.agent_ref, body.a.config)
        _, b_config = suite_inputs(services, body.dataset, body.b.agent_ref, body.b.config)
        name = body.name or f"{a_config.ref} vs {b_config.ref}"
        return services.plan_ablation(name, dataset, a_config, b_config)
    except UnknownExperimentError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except DatasetError as exc:
        raise InvalidInput("the dataset cannot be ablated with these agents", exc.issues) from exc
    except (ValueError, LookupError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("", status_code=202)
async def create_ablation(
    body: AblationCreate, services: ServicesDep, jobs: JobsDep
) -> JobAccepted:
    """Evaluate every variant in the background; poll the job, then
    GET /api/ablations/{resource_id}. With `experiment_id`, that comparison's A and B suites
    are reused; with `dataset`, `a` and `b`, both sides are evaluated first."""
    # Loading suites and linting every variant is blocking work: keep it off the event loop.
    plan = await run_in_threadpool(_plan, services, body)

    async def work(job: Job) -> None:
        ablation = await services.execute_ablation(plan, progress=job.report)
        # Cases the harness or the provider broke make the job fail; the result stays stored.
        ensure_ablation_judged(ablation)

    return accepted(submit(jobs, "ablation", plan.ablation_id, work, total=plan.total))


@router.get("/{ablation_id}")
def get_ablation(ablation_id: str, services: ServicesDep) -> Ablation:
    ablation = services.repository.get_ablation(ablation_id)
    if ablation is None:
        raise HTTPException(status_code=404, detail="ablation not found")
    return ablation
