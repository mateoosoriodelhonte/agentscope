"""FastAPI application factory."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from datetime import timedelta
from importlib import resources
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from agentscope import __version__
from agentscope.api.deps import InvalidInput, invalid_input_response
from agentscope.api.routes import (
    agents,
    datasets,
    demo,
    evaluations,
    experiments,
    insights,
    jobs,
    runs,
    settings,
)
from agentscope.api.security import LocalOnlyMiddleware, security_header_dict
from agentscope.datasets.issues import Issue
from agentscope.jobs import JobManager
from agentscope.services import Services, build_services

_log = logging.getLogger(__name__)

# How often a running server looks for work whose process died (see Services.recover_interrupted).
SWEEP_INTERVAL = timedelta(minutes=1)


def find_web_dist() -> Path | None:
    """The built dashboard: packaged with the wheel, or frontend/dist in a checkout."""
    packaged = Path(str(resources.files("agentscope").joinpath("web", "dist")))
    if (packaged / "index.html").exists():
        return packaged
    checkout = Path(__file__).resolve().parents[3] / "frontend" / "dist"
    if (checkout / "index.html").exists():
        return checkout
    return None


def _recover(services: Services) -> None:
    try:
        services.recover_interrupted()
    except Exception:  # never keep the server from starting or serving over housekeeping
        _log.warning("could not check for interrupted work", exc_info=True)


async def _sweep_interrupted(services: Services) -> None:
    while True:
        await asyncio.sleep(SWEEP_INTERVAL.total_seconds())
        await asyncio.to_thread(_recover, services)


def _request_issues(exc: RequestValidationError) -> InvalidInput:
    """Request validation errors as located issues, without echoing the submitted values."""
    issues = [
        Issue("/" + "/".join(str(part) for part in error.get("loc", ())), str(error.get("msg")))
        for error in exc.errors()[:50]
    ]
    return InvalidInput("the request is invalid", issues)


def create_app(
    services: Services | None = None,
    *,
    web_dist: Path | None = None,
    api_token: str | None = None,
    allowed_hosts: Sequence[str] = (),
    jobs_manager: JobManager | None = None,
) -> FastAPI:
    """`api_token` turns on remote mode: every request must then authenticate with it."""
    services = services or build_services()
    job_manager = jobs_manager or JobManager(redact=services.redactor.redact_text)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        # Work whose process died (before this start, or while this server runs, e.g. a
        # killed CLI evaluation) is marked interrupted; live work in any process is not.
        await asyncio.to_thread(_recover, services)
        sweeper = asyncio.create_task(_sweep_interrupted(services))
        try:
            yield
        finally:
            sweeper.cancel()
            await job_manager.shutdown()

    app = FastAPI(
        title="AgentScope",
        version=__version__,
        # Swagger UI loads its assets from a CDN, which the strict CSP (correctly) blocks;
        # the OpenAPI document below is served locally instead.
        docs_url=None,
        redoc_url=None,
        openapi_url="/api/openapi.json",
        lifespan=lifespan,
    )
    app.state.services = services
    app.state.jobs = job_manager
    app.add_middleware(
        LocalOnlyMiddleware,
        allowed_hosts=allowed_hosts,
        allowed_origins=services.settings.allowed_origins,
        max_body_bytes=256 * 1024,
        large_body_paths=["/api/datasets"],
        large_body_bytes=services.settings.max_request_bytes,
        api_token=api_token,
    )

    @app.exception_handler(InvalidInput)
    async def invalid_input(_request: Request, exc: InvalidInput) -> JSONResponse:
        return invalid_input_response(exc)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(_request: Request, exc: RequestValidationError) -> JSONResponse:
        return invalid_input_response(_request_issues(exc))

    @app.exception_handler(Exception)
    async def internal_error(request: Request, exc: Exception) -> JSONResponse:
        # Never a traceback in a response: the details go to the server log only.
        _log.error("unhandled %s on %s %s", type(exc).__name__, request.method, request.url.path)
        # This handler runs outside LocalOnlyMiddleware, so it adds the security headers.
        return JSONResponse(
            {"detail": "internal error"}, status_code=500, headers=security_header_dict()
        )

    @app.get("/api/health", tags=["meta"])
    def health() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    for module in (
        agents,
        runs,
        datasets,
        evaluations,
        experiments,
        insights,
        jobs,
        settings,
        demo,
    ):
        app.include_router(module.router)

    @app.api_route(
        "/api/{path:path}",
        methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        include_in_schema=False,
    )
    def api_not_found(path: str) -> JSONResponse:
        return JSONResponse({"detail": "not found"}, status_code=404)

    dist = web_dist if web_dist is not None else find_web_dist()
    if dist is not None and (dist / "index.html").is_file():
        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")
        index = dist / "index.html"

        @app.get("/{path:path}", include_in_schema=False)
        def spa(path: str) -> FileResponse:
            candidate = (dist / path).resolve()
            if path and candidate.is_file() and dist.resolve() in candidate.parents:
                return FileResponse(candidate)
            return FileResponse(index)

    return app
