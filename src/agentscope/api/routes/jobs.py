from __future__ import annotations

from fastapi import APIRouter, HTTPException

from agentscope.api.deps import JobsDep
from agentscope.api.schemas import JobStatus

router = APIRouter(prefix="/api/jobs", tags=["jobs"])


@router.get("/{job_id}")
def get_job(job_id: str, jobs: JobsDep) -> JobStatus:
    """Jobs live in server memory: after a restart their ids are unknown (404), while the
    resources they created stay in storage."""
    job = jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    return job.status()
