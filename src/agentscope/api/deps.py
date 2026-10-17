"""FastAPI dependencies and small response helpers shared by the routes."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Annotated, cast

from fastapi import Depends, HTTPException, Request
from fastapi.responses import JSONResponse, Response

from agentscope.api.schemas import JobAccepted, JobKind
from agentscope.datasets.issues import Issue
from agentscope.jobs import Job, JobManager, QueueFull, Work
from agentscope.services import Services, validation_issue


def get_services(request: Request) -> Services:
    return cast(Services, request.app.state.services)


def get_jobs(request: Request) -> JobManager:
    return cast(JobManager, request.app.state.jobs)


ServicesDep = Annotated[Services, Depends(get_services)]
JobsDep = Annotated[JobManager, Depends(get_jobs)]


def submit(jobs: JobManager, kind: JobKind, resource_id: str, work: Work, total: int = 0) -> Job:
    try:
        return jobs.submit(kind, resource_id, work, total=total)
    except QueueFull as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc


def accepted(job: Job) -> JobAccepted:
    return JobAccepted(job_id=job.job_id, kind=job.kind, resource_id=job.resource_id)


class InvalidInput(Exception):
    """Answered as 422 `{"detail": ..., "issues": [ValidationIssue, ...]}`."""

    def __init__(self, detail: str, issues: Sequence[Issue]) -> None:
        super().__init__(detail)
        self.detail = detail
        self.issues = list(issues)


def invalid_input_response(exc: InvalidInput) -> JSONResponse:
    return JSONResponse(
        {
            "detail": exc.detail,
            "issues": [validation_issue(i).model_dump(mode="json") for i in exc.issues],
        },
        status_code=422,
    )


def download(content: bytes, filename: str) -> Response:
    """A JSON attachment. `filename` must already be a safe plain name (ids and slugs)."""
    return Response(
        content,
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
