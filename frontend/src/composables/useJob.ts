import { onBeforeUnmount, ref, shallowRef, type Ref, type ShallowRef } from 'vue'
import { pollJob } from '@/api/client'
import type { JobAccepted, JobStatus } from '@/api/types'

export interface JobRunner {
  /** The latest polled status; null before the first poll. */
  job: ShallowRef<JobStatus | null>
  running: Ref<boolean>
  error: ShallowRef<unknown>
  /** Starts the job and polls it to the end; resolves null when it failed or polling stopped. */
  start(begin: () => Promise<JobAccepted>): Promise<JobStatus | null>
  /** Polls a job that was started elsewhere (e.g. before navigating to this page). */
  follow(jobId: string): Promise<JobStatus | null>
}

/**
 * Runs one long API job (a run, an evaluation, an experiment, the demo seed): POST, then poll
 * GET /api/jobs/{id} until it ends. Polling stops when the component unmounts; the job itself
 * keeps running on the server.
 */
export function useJob(): JobRunner {
  const job = shallowRef<JobStatus | null>(null)
  const running = ref(false)
  const error = shallowRef<unknown>(null)
  let controller: AbortController | null = null

  async function track(jobId: () => Promise<string>): Promise<JobStatus | null> {
    controller?.abort()
    const current = new AbortController()
    controller = current
    job.value = null
    error.value = null
    running.value = true
    try {
      const id = await jobId()
      if (current.signal.aborted) return null
      return await pollJob(id, { signal: current.signal, onProgress: (status) => (job.value = status) })
    } catch (e) {
      if (!current.signal.aborted) error.value = e
      return null
    } finally {
      if (!current.signal.aborted) running.value = false
    }
  }

  onBeforeUnmount(() => controller?.abort())
  return {
    job,
    running,
    error,
    start: (begin) => track(async () => (await begin()).job_id),
    follow: (jobId) => track(async () => jobId),
  }
}

/** "3/10" for jobs that report progress, otherwise the state. */
export function jobProgress(job: JobStatus | null): string {
  if (!job) return 'starting'
  if (job.total > 0) return `${job.progress}/${job.total}`
  return job.state
}
