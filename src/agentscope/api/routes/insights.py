"""Read-only aggregations: the overview and failure analysis."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query
from fastapi.concurrency import run_in_threadpool

from agentscope.analysis import failure_analysis, overview
from agentscope.api.deps import ServicesDep
from agentscope.api.schemas import FailureAnalysis, Overview

router = APIRouter(prefix="/api", tags=["insights"])


@router.get("/overview")
async def get_overview(services: ServicesDep) -> Overview:
    # Async only for the provider probes; the database reads run in the thread pool.
    providers = await services.provider_statuses()
    return await run_in_threadpool(overview, services.repository, providers)


@router.get("/failures")
def get_failures(
    services: ServicesDep,
    agent_ref: str | None = None,
    suite_id: Annotated[list[str] | None, Query()] = None,
) -> FailureAnalysis:
    """By default the latest completed evaluation of each agent version; repeat `suite_id`
    to choose evaluations, and use `agent_ref` to narrow either selection."""
    return failure_analysis(
        services.repository,
        suite_ids=suite_id or [],
        agent_refs=[agent_ref] if agent_ref else [],
    )
