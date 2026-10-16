from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from agentscope.api.deps import get_services
from agentscope.api.schemas import AgentInfo, AgentsResponse
from agentscope.services import Services

router = APIRouter(prefix="/api/agents", tags=["agents"])


@router.get("")
def list_agents(services: Annotated[Services, Depends(get_services)]) -> AgentsResponse:
    agents = [
        AgentInfo(
            name=d.name,
            title=d.title,
            description=d.description,
            toolset=list(d.toolset),
            output_schema=d.output_schema(),
            prompts=dict(d.prompts),
            versions=list(d.versions),
        )
        for d in services.catalog.definitions()
    ]
    return AgentsResponse(agents=agents, tools=services.tools.all_specs())
