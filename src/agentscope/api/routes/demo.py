from __future__ import annotations

from fastapi import APIRouter

from agentscope.api.deps import JobsDep, ServicesDep, accepted, submit
from agentscope.api.schemas import JobAccepted
from agentscope.demo import demo_steps, seed_demo
from agentscope.jobs import Job

router = APIRouter(prefix="/api/demo", tags=["demo"])

# The demo seed creates many resources; the job names this placeholder instead of one id.
DEMO_RESOURCE = "demo"


@router.post("/seed", status_code=202)
async def seed(services: ServicesDep, jobs: JobsDep) -> JobAccepted:
    """Idempotent. While a seed is already in progress, that job is returned again."""
    running = jobs.active("demo_seed")
    if running:
        return accepted(running[0])

    async def work(job: Job) -> None:
        await seed_demo(services, progress=job.report)

    return accepted(submit(jobs, "demo_seed", DEMO_RESOURCE, work, total=demo_steps()))
