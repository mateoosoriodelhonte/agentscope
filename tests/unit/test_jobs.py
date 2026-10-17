from __future__ import annotations

import asyncio

import pytest

from agentscope.jobs import CANCELLED, Job, JobManager, QueueFull


async def settle() -> None:
    """Let every ready task run until it blocks."""
    for _ in range(5):
        await asyncio.sleep(0)


async def _finished(jobs: JobManager, job_id: str) -> None:
    # Polling is how clients follow jobs: the manager deliberately has no completion hook.
    while (job := jobs.get(job_id)) is not None and job.finished_at is None:  # noqa: ASYNC110
        await asyncio.sleep(0.001)


async def test_job_succeeds_with_progress() -> None:
    jobs = JobManager()

    async def work(job: Job) -> None:
        for done in range(1, 4):
            job.report(done, 3)
            await asyncio.sleep(0)

    job = jobs.submit("evaluation", "suite-1", work, total=3)
    assert job.status().state == "queued"
    await asyncio.wait_for(_finished(jobs, job.job_id), 5)
    status = job.status()
    assert (status.state, status.progress, status.total) == ("succeeded", 3, 3)
    assert status.finished_at is not None
    assert status.error is None
    assert jobs.get(job.job_id) is job
    assert jobs.get("unknown") is None


async def test_at_most_two_jobs_run_at_once() -> None:
    jobs = JobManager(max_running=2, max_queued=5)
    release = asyncio.Event()
    running = 0
    peak = 0

    async def work(_job: Job) -> None:
        nonlocal running, peak
        running += 1
        peak = max(peak, running)
        await release.wait()
        running -= 1

    submitted = [jobs.submit("run", f"r{i}", work) for i in range(5)]
    await settle()
    assert [j.state for j in submitted] == ["running", "running", "queued", "queued", "queued"]
    release.set()
    for job in submitted:
        await asyncio.wait_for(_finished(jobs, job.job_id), 5)
    assert peak == 2
    assert all(j.state == "succeeded" for j in submitted)


async def test_a_full_queue_refuses_new_jobs() -> None:
    jobs = JobManager(max_running=1, max_queued=1)
    release = asyncio.Event()

    async def work(_job: Job) -> None:
        await release.wait()

    jobs.submit("run", "a", work)
    jobs.submit("run", "b", work)
    with pytest.raises(QueueFull):
        jobs.submit("run", "c", work)
    assert len(jobs.active()) == 2
    assert jobs.active("evaluation") == []
    release.set()
    await jobs.shutdown()
    jobs.submit("run", "d", work)  # capacity is back once jobs finish
    await jobs.shutdown()


async def test_failures_are_recorded_with_a_redacted_message() -> None:
    jobs = JobManager(redact=lambda text: text.replace("hunter2", "[REDACTED]"))

    async def work(_job: Job) -> None:
        raise ValueError("password hunter2 rejected")

    job = jobs.submit("experiment", "x", work)
    await asyncio.wait_for(_finished(jobs, job.job_id), 5)
    assert job.state == "failed"
    assert job.error == "ValueError: password [REDACTED] rejected"


async def test_shutdown_cancels_running_and_queued_jobs() -> None:
    jobs = JobManager(max_running=1)
    started = asyncio.Event()
    cleaned_up = False

    async def work(_job: Job) -> None:
        nonlocal cleaned_up
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cleaned_up = True

    running = jobs.submit("evaluation", "a", work)
    queued = jobs.submit("evaluation", "b", work)
    await started.wait()
    await jobs.shutdown()
    assert cleaned_up
    for job in (running, queued):
        assert job.state == "failed"
        assert job.error == CANCELLED
    assert jobs.active() == []


async def test_finished_jobs_are_forgotten_beyond_the_limit() -> None:
    jobs = JobManager(keep_finished=2)

    async def work(_job: Job) -> None:
        return None

    ids = []
    for index in range(4):
        job = jobs.submit("run", str(index), work)
        ids.append(job.job_id)
        await asyncio.wait_for(_finished(jobs, job.job_id), 5)
    jobs.submit("run", "last", work)
    assert jobs.get(ids[0]) is None
    assert jobs.get(ids[1]) is None
    assert jobs.get(ids[3]) is not None
    await jobs.shutdown()
