from __future__ import annotations

from datetime import UTC

from fastapi import APIRouter
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse

from agentscope.api.deps import JobsDep, ServicesDep
from agentscope.api.schemas import PurgeConflict, PurgeRequest, PurgeResult, SettingsView
from agentscope.storage.repository import PurgeIncomplete

router = APIRouter(prefix="/api/settings", tags=["settings"])


@router.get("")
def get_settings(services: ServicesDep) -> SettingsView:
    """Read-only: settings come from the environment, never from the API."""
    return services.settings_view()


@router.post(
    "/purge",
    response_model=PurgeResult,
    responses={
        409: {
            "model": PurgeConflict,
            "description": "`jobs_running`: refused, nothing deleted (no result). "
            "`purge_incomplete`: rows deleted (result), old contents still on disk.",
        }
    },
)
async def purge(
    body: PurgeRequest, services: ServicesDep, jobs: JobsDep
) -> PurgeResult | JSONResponse:
    """The packaged datasets are kept (see Services.purge)."""
    if jobs.active():
        # A running suite would try to record cases into rows that were just deleted.
        refused = PurgeConflict(
            detail="jobs are still running; nothing was deleted; purge once they have finished",
            code="jobs_running",
        )
        return JSONResponse(refused.model_dump(mode="json", exclude_none=True), status_code=409)
    before = body.before
    if before is not None and before.tzinfo is None:
        before = before.replace(tzinfo=UTC)
    try:
        return await run_in_threadpool(services.purge, body.scope, before)
    except PurgeIncomplete as exc:
        partial = PurgeConflict(detail=str(exc), code="purge_incomplete", result=exc.result)
        return JSONResponse(partial.model_dump(mode="json"), status_code=409)
