"""In-process background jobs for long-running API work: runs, evaluations, experiments and
demo seeding.

Jobs live in this process's memory only, so a restart forgets them. What a job creates is
persisted as it goes, and suites a restart cut short are marked as errors at startup. At most
`max_running` jobs run at once and up to `max_queued` more wait their turn; beyond that,
`submit` raises QueueFull (HTTP 429) instead of queueing without bound. Shutdown cancels and
awaits every unfinished job.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime

from agentscope.api.schemas import JobKind, JobState, JobStatus
from agentscope.domain.common import utcnow

MAX_ERROR_CHARS = 500
CANCELLED = "cancelled: AgentScope stopped before the job finished"


class QueueFull(RuntimeError):
    """Too many jobs are running or waiting; try again once some finish."""


@dataclass
class Job:
    kind: JobKind
    resource_id: str
    job_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    created_at: datetime = field(default_factory=utcnow)
    state: JobState = "queued"
    progress: int = 0
    total: int = 0
    error: str | None = None
    finished_at: datetime | None = None

    def report(self, done: int, total: int) -> None:
        """Progress callback for the work: `done` of `total` units (cases) finished."""
        self.progress, self.total = done, total

    def status(self) -> JobStatus:
        return JobStatus(
            job_id=self.job_id,
            kind=self.kind,
            state=self.state,
            resource_id=self.resource_id,
            created_at=self.created_at,
            finished_at=self.finished_at,
            progress=self.progress,
            total=self.total,
            error=self.error,
        )


Work = Callable[[Job], Awaitable[object]]


class JobManager:
    def __init__(
        self,
        *,
        max_running: int = 2,
        max_queued: int = 16,
        keep_finished: int = 200,
        redact: Callable[[str], str] = lambda text: text,
    ) -> None:
        self._semaphore = asyncio.Semaphore(max_running)
        self._capacity = max_running + max_queued
        self._keep_finished = keep_finished
        self._redact = redact
        self._jobs: dict[str, Job] = {}
        self._tasks: dict[str, asyncio.Task[None]] = {}

    def submit(self, kind: JobKind, resource_id: str, work: Work, *, total: int = 0) -> Job:
        """Start `work` in the background. Call from the event loop that should run it."""
        if len(self._tasks) >= self._capacity:
            raise QueueFull(
                f"{len(self._tasks)} jobs are already running or queued; try again shortly"
            )
        job = Job(kind=kind, resource_id=resource_id, total=total)
        self._jobs[job.job_id] = job
        task = asyncio.get_running_loop().create_task(self._run(job, work))
        self._tasks[job.job_id] = task
        task.add_done_callback(lambda _: self._tasks.pop(job.job_id, None))
        self._forget_old()
        return job

    def get(self, job_id: str) -> Job | None:
        return self._jobs.get(job_id)

    def active(self, kind: JobKind | None = None) -> list[Job]:
        """Unfinished jobs (of one kind), oldest first."""
        return [
            j
            for j in self._jobs.values()
            if j.finished_at is None and (kind is None or j.kind == kind)
        ]

    async def shutdown(self) -> None:
        tasks = list(self._tasks.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    async def _run(self, job: Job, work: Work) -> None:
        try:
            async with self._semaphore:
                job.state = "running"
                await work(job)
        except asyncio.CancelledError:
            self._finish(job, CANCELLED)
            raise
        except Exception as exc:
            message = self._redact(str(exc))
            self._finish(job, f"{type(exc).__name__}: {message}" if message else type(exc).__name__)
        else:
            self._finish(job, None)

    def _finish(self, job: Job, error: str | None) -> None:
        job.state = "failed" if error is not None else "succeeded"
        job.error = error[:MAX_ERROR_CHARS] if error is not None else None
        job.finished_at = utcnow()

    def _forget_old(self) -> None:
        finished = [j.job_id for j in self._jobs.values() if j.finished_at is not None]
        for job_id in finished[: max(0, len(finished) - self._keep_finished)]:
            del self._jobs[job_id]
