"""FastAPI application factory."""

from __future__ import annotations

from importlib import resources
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from agentscope import __version__
from agentscope.api.routes import agents, runs
from agentscope.api.security import LocalOnlyMiddleware
from agentscope.services import Services, build_services


def find_web_dist() -> Path | None:
    """The built dashboard: packaged with the wheel, or frontend/dist in a checkout."""
    packaged = Path(str(resources.files("agentscope").joinpath("web", "dist")))
    if (packaged / "index.html").exists():
        return packaged
    checkout = Path(__file__).resolve().parents[3] / "frontend" / "dist"
    if (checkout / "index.html").exists():
        return checkout
    return None


def create_app(services: Services | None = None, *, web_dist: Path | None = None) -> FastAPI:
    services = services or build_services()
    app = FastAPI(
        title="AgentScope",
        version=__version__,
        docs_url="/api/docs",
        redoc_url=None,
        openapi_url="/api/openapi.json",
    )
    app.state.services = services
    settings = services.settings
    app.add_middleware(
        LocalOnlyMiddleware,
        allowed_origins=settings.allowed_origins,
        max_body_bytes=256 * 1024,
        large_body_paths=["/api/datasets"],
        large_body_bytes=settings.max_request_bytes,
    )

    @app.get("/api/health", tags=["meta"])
    def health() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    app.include_router(agents.router)
    app.include_router(runs.router)

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
