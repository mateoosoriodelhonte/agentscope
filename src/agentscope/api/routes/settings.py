from __future__ import annotations

from datetime import UTC

from fastapi import APIRouter, HTTPException
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse

from agentscope.api.deps import JobsDep, ServicesDep
from agentscope.api.schemas import PurgeRequest, PurgeResult, SettingsView
from agentscope.storage.repository import PurgeIncomplete

router = APIRouter(prefix="/api/settings", tags=["settings"])


@router.get("")
def get_settings(services: ServicesDep) -> SettingsView:
    """Read-only: settings come from the environment, never from the API."""
    return services.settings_view()


@router.post(
    "/purge",
    response_model=PurgeResult,
    responses={409: {"description": "jobs running, or purge incomplete (with its result)"}},
)
async def purge(
    body: PurgeRequest, services: ServicesDep, jobs: JobsDep
) -> PurgeResult | JSONResponse:
    if jobs.active():
        # A running suite would try to record cases into rows that were just deleted.
        raise HTTPException(
            status_code=409, detail="jobs are still running; purge once they have finished"
        )
    before = body.before
    if before is not None and before.tzinfo is None:
        before = before.replace(tzinfo=UTC)
    try:
        return await run_in_threadpool(services.repository.purge, body.scope, before)
    except PurgeIncomplete as exc:
        return JSONResponse(
            {"detail": str(exc), "result": exc.result.model_dump(mode="json")}, status_code=409
        )
