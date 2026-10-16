import type {
  AgentsResponse,
  Comparison,
  Dataset,
  DatasetDetail,
  DatasetSummary,
  DatasetValidation,
  EvaluationCreate,
  ExperimentCreate,
  ExperimentListItem,
  FailureAnalysis,
  Health,
  JobAccepted,
  JobStatus,
  Overview,
  ProviderStatus,
  PurgeRequest,
  PurgeResult,
  RunCreate,
  RunDetail,
  RunList,
  RunStatus,
  SettingsView,
  SuiteListItem,
  SuiteResult,
} from './types'

export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message)
    this.name = 'ApiError'
  }
}

/** A long-running job ended in the `failed` state. */
export class JobFailedError extends Error {
  constructor(readonly job: JobStatus) {
    super(job.error ?? 'job failed')
    this.name = 'JobFailedError'
  }
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers)
  headers.set('Accept', 'application/json')
  if (init.method && init.method !== 'GET') {
    headers.set('Content-Type', 'application/json')
    // Required by the API's mutation guard; forces a CORS preflight cross-origin.
    headers.set('X-AgentScope-Client', '1')
  }
  const response = await fetch(path, { ...init, headers, credentials: 'same-origin' })
  if (!response.ok) {
    let detail = response.statusText || `HTTP ${response.status}`
    try {
      const body = (await response.json()) as { detail?: unknown }
      if (typeof body.detail === 'string') detail = body.detail
    } catch {
      /* non-JSON error body */
    }
    throw new ApiError(response.status, detail)
  }
  return (await response.json()) as T
}

function post<T>(path: string, body: unknown = {}, signal?: AbortSignal): Promise<T> {
  return request<T>(path, { method: 'POST', body: JSON.stringify(body), signal })
}

/**
 * One encoded path segment. "." and ".." survive encodeURIComponent (and their %2E forms are
 * still dot segments to the URL parser), so they would climb out of the intended route.
 */
function seg(value: string): string {
  if (value === '' || value === '.' || value === '..') {
    throw new ApiError(400, `invalid path segment: ${JSON.stringify(value)}`)
  }
  return encodeURIComponent(value)
}

export type Verdict = 'passed' | 'failed' | 'none'

/** Filters for GET /api/runs. Undefined fields are not sent. */
export interface RunListQuery {
  agent?: string
  agent_version?: string
  status?: RunStatus
  has_faults?: boolean
  has_blocked_calls?: boolean
  verdict?: Verdict
  case_id?: string
  suite_id?: string
  limit?: number
  offset?: number
}

function queryString(params: object): string {
  const search = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== '') search.set(key, String(value))
  }
  const text = search.toString()
  return text ? `?${text}` : ''
}

// Functions that take path segments are async so an invalid segment rejects like any other
// request failure instead of throwing synchronously.
export const api = {
  health: (signal?: AbortSignal) => request<Health>('/api/health', { signal }),
  overview: (signal?: AbortSignal) => request<Overview>('/api/overview', { signal }),
  agents: (signal?: AbortSignal) => request<AgentsResponse>('/api/agents', { signal }),
  providers: (signal?: AbortSignal) => request<ProviderStatus[]>('/api/providers', { signal }),
  job: async (jobId: string, signal?: AbortSignal) =>
    request<JobStatus>(`/api/jobs/${seg(jobId)}`, { signal }),

  listRuns: (query: RunListQuery = {}, signal?: AbortSignal) =>
    request<RunList>(`/api/runs${queryString(query)}`, { signal }),
  createRun: (body: RunCreate) => post<JobAccepted>('/api/runs', body),
  getRun: async (runId: string, signal?: AbortSignal) =>
    request<RunDetail>(`/api/runs/${seg(runId)}`, { signal }),
  runExportUrl: (runId: string) => `/api/runs/${seg(runId)}/export`,

  listDatasets: (signal?: AbortSignal) => request<DatasetSummary[]>('/api/datasets', { signal }),
  importDataset: (dataset: Dataset) => post<DatasetSummary>('/api/datasets', dataset),
  /** Lints an untrusted document without importing it. */
  validateDataset: (document: unknown) =>
    post<DatasetValidation>('/api/datasets/validate', document),
  getDataset: async (name: string, version: string, signal?: AbortSignal) =>
    request<DatasetDetail>(`/api/datasets/${seg(name)}/${seg(version)}`, { signal }),
  datasetExportUrl: (name: string, version: string) =>
    `/api/datasets/${seg(name)}/${seg(version)}/export`,

  listEvaluations: (signal?: AbortSignal) =>
    request<SuiteListItem[]>('/api/evaluations', { signal }),
  createEvaluation: (body: EvaluationCreate) => post<JobAccepted>('/api/evaluations', body),
  getEvaluation: async (suiteId: string, signal?: AbortSignal) =>
    request<SuiteResult>(`/api/evaluations/${seg(suiteId)}`, { signal }),
  evaluationExportUrl: (suiteId: string) => `/api/evaluations/${seg(suiteId)}/export`,

  listExperiments: (signal?: AbortSignal) =>
    request<ExperimentListItem[]>('/api/experiments', { signal }),
  createExperiment: (body: ExperimentCreate) => post<JobAccepted>('/api/experiments', body),
  getExperiment: async (experimentId: string, signal?: AbortSignal) =>
    request<Comparison>(`/api/experiments/${seg(experimentId)}`, { signal }),

  failures: (signal?: AbortSignal) => request<FailureAnalysis>('/api/failures', { signal }),

  settings: (signal?: AbortSignal) => request<SettingsView>('/api/settings', { signal }),
  purge: (body: PurgeRequest) => post<PurgeResult>('/api/settings/purge', body),

  seedDemo: () => post<JobAccepted>('/api/demo/seed'),
}

export interface PollOptions {
  signal?: AbortSignal
  initialDelayMs?: number
  maxDelayMs?: number
  factor?: number
  onProgress?: (job: JobStatus) => void
}

function sleep(ms: number, signal?: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) {
      reject(signal.reason)
      return
    }
    const onAbort = () => {
      clearTimeout(timer)
      reject(signal?.reason)
    }
    const timer = setTimeout(() => {
      signal?.removeEventListener('abort', onAbort)
      resolve()
    }, ms)
    signal?.addEventListener('abort', onAbort, { once: true })
  })
}

/**
 * Polls GET /api/jobs/{id} with exponential backoff until the job succeeds (resolves) or
 * fails (rejects with JobFailedError). Aborting the signal stops polling with its reason.
 */
export async function pollJob(jobId: string, options: PollOptions = {}): Promise<JobStatus> {
  const { signal, initialDelayMs = 250, maxDelayMs = 2000, factor = 1.6, onProgress } = options
  let delay = initialDelayMs
  for (;;) {
    const job = await api.job(jobId, signal)
    onProgress?.(job)
    if (job.state === 'succeeded') return job
    if (job.state === 'failed') throw new JobFailedError(job)
    await sleep(delay, signal)
    delay = Math.min(maxDelayMs, Math.round(delay * factor))
  }
}
