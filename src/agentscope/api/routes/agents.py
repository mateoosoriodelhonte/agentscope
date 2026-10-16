from __future__ import annotations

from fastapi import APIRouter

from agentscope.api.deps import ServicesDep
from agentscope.api.schemas import AgentsResponse, ProviderStatus

router = APIRouter(prefix="/api", tags=["agents"])


@router.get("/agents")
def list_agents(services: ServicesDep) -> AgentsResponse:
    return services.agents()


@router.get("/providers")
async def list_providers(services: ServicesDep) -> list[ProviderStatus]:
    """Probes enabled providers at their configured targets only (async network I/O)."""
    return await services.provider_statuses()
