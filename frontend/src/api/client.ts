import type {
  AgentsResponse,
  Comparison,
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
  SuiteStatus,
  ValidationIssue,
} from './types'

export class ApiError extends Error {
  /**
   * Why the server refused the request, located by path: the `issues` of a 422 body. The
   * `detail` (the message) only says that the request is invalid, never why.
   */
  readonly issues: ValidationIssue[]

  constructor(
    readonly status: number,
    message: string,
    /** The parsed JSON error body, when there was one (e.g. a partial purge result). */
    readonly body: unknown = null,
  ) {
    super(message)
    this.name = 'ApiError'
    this.issues = errorIssues(body)
  }
}

/** Request validation locates fields under /body; forms name them from the body's root. */
function bodyPath(path: string): string {
  if (path === '/body') return '/'
  return path.startsWith('/body/') ? path.slice('/body'.length) : path
}

/** The `issues` list of an error body (ValidationIssue), skipping anything malformed. */
function errorIssues(body: unknown): ValidationIssue[] {
  if (body === null || typeof body !== 'object') return []
  const raw = (body as { issues?: unknown }).issues
  if (!Array.isArray(raw)) return []
  return raw.flatMap((item: unknown): ValidationIssue[] => {
    if (item === null || typeof item !== 'object') return []
    const { path, message, severity } = item as { path?: unknown; message?: unknown; severity?: unknown }
    if (typeof message !== 'string') return []
    return [
      {
        path: bodyPath(typeof path === 'string' ? path : ''),
        message,
        severity: severity === 'warning' ? 'warning' : 'error',
      },
    ]
  })
}

/**
 * A readable message from a FastAPI error body: `detail` as a string, as an object with a
 * `message`, or as a list of validation errors (`loc` and `msg`).
 */
function errorDetail(body: unknown): string | null {
  if (body === null || typeof body !== 'object') return null
  const detail = (body as { detail?: unknown }).detail
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail)) {
    const parts = detail.map((item: unknown) => {
      if (item === null || typeof item !== 'object') return String(item)
      const { loc, msg } = item as { loc?: unknown; msg?: unknown }
      const where = Array.isArray(loc) ? loc.filter((part) => part !== 'body').join('.') : ''
      return where ? `${where}: ${String(msg)}` : String(msg)
    })
    return parts.length ? parts.join('; ') : null
  }
  if (detail !== null && typeof detail === 'object') {
    const message = (detail as { message?: unknown }).message
    if (typeof message === 'string') return message
  }
  return null
}

/** A long-running job ended in the `failed` state. */
export class JobFailedError extends Error {
  constructor(readonly job: JobStatus) {
    super(job.error ?? 'job failed')
    this.name = 'JobFailedError'
  }
}

/** Sends one request; an error status rejects with ApiError. */
async function send(path: string, init: RequestInit = {}): Promise<Response> {
  const headers = new Headers(init.headers)
  headers.set('Accept', 'application/json')
  if (init.method && init.method !== 'GET') {
    headers.set('Content-Type', 'application/json')
    // Required by the API's mutation guard; forces a CORS preflight cross-origin.
    headers.set('X-AgentScope-Client', '1')
  }
  const response = await fetch(path, { ...init, headers, credentials: 'same-origin' })
  if (!response.ok) {
    let body: unknown = null
    try {
      body = await response.json()
    } catch {
      /* non-JSON error body */
    }
    const message = errorDetail(body) ?? (response.statusText || `HTTP ${response.status}`)
    throw new ApiError(response.status, message, body)
  }
  return response
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  return (await (await send(path, init)).json()) as T
}

/** One page of a list endpoint. `total` is null when the server did not say how many exist. */
export interface Page<T> {
  items: T[]
  total: number | null
}

/**
 * A list endpoint that pages: the body is the page (a bare list, or `{items, total}`) and the
 * total comes from `X-Total-Count` when the body does not carry it.
 */
async function requestPage<T>(path: string, signal?: AbortSignal): Promise<Page<T>> {
  const response = await send(path, { signal })
  const body = (await response.json()) as unknown
  const header = response.headers.get('X-Total-Count')
  const counted = header === null || header.trim() === '' ? NaN : Number(header)
  let items: T[] = []
  let total: number | null = Number.isInteger(counted) && counted >= 0 ? counted : null
  if (Array.isArray(body)) {
    items = body as T[]
  } else if (body !== null && typeof body === 'object' && Array.isArray((body as { items?: unknown }).items)) {
    const page = body as { items: T[]; total?: unknown }
    items = page.items
    if (typeof page.total === 'number' && Number.isInteger(page.total) && page.total >= 0) total = page.total
  }
  return { items, total }
}

function post<T>(path: string, body: unknown = {}, signal?: AbortSignal): Promise<T> {
  return request<T>(path, { method: 'POST', body: JSON.stringify(body), signal })
}

/**
 * Posts a document exactly as the user wrote it. Parsing and re-serialising it in the browser
 * would hide what the server's strict loader rejects (duplicate keys, non-finite numbers) and
 * store different content than the file.
 */
function postText<T>(path: string, text: string): Promise<T> {
  return request<T>(path, { method: 'POST', body: text })
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

/** Filters for GET /api/evaluations. `dataset` is a dataset name (every version). */
export interface SuiteListQuery {
  agent_ref?: string
  dataset?: string
  status?: SuiteStatus
  limit?: number
  offset?: number
}

/** Paging for GET /api/experiments. */
export interface ExperimentListQuery {
  limit?: number
  offset?: number
}

/** The most rows one list request may ask for (the API's own cap). */
export const MAX_PAGE = 200

/**
 * Filters for GET /api/failures. Undefined fields are not sent; a server that does not
 * support a filter ignores it, so callers check what came back.
 */
export interface FailureQuery {
  agent_ref?: string
  suite_id?: string
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
  /** Imports the dataset document `source` (JSON text), sent byte for byte. */
  importDataset: (source: string) => postText<DatasetSummary>('/api/datasets', source),
  /** Lints an untrusted document (JSON text, sent byte for byte) without importing it. */
  validateDataset: (source: string) => postText<DatasetValidation>('/api/datasets/validate', source),
  getDataset: async (name: string, version: string, signal?: AbortSignal) =>
    request<DatasetDetail>(`/api/datasets/${seg(name)}/${seg(version)}`, { signal }),
  datasetExportUrl: (name: string, version: string) =>
    `/api/datasets/${seg(name)}/${seg(version)}/export`,

  listEvaluations: (query: SuiteListQuery = {}, signal?: AbortSignal) =>
    requestPage<SuiteListItem>(`/api/evaluations${queryString(query)}`, signal),
  createEvaluation: (body: EvaluationCreate) => post<JobAccepted>('/api/evaluations', body),
  getEvaluation: async (suiteId: string, signal?: AbortSignal) =>
    request<SuiteResult>(`/api/evaluations/${seg(suiteId)}`, { signal }),
  evaluationExportUrl: (suiteId: string) => `/api/evaluations/${seg(suiteId)}/export`,

  listExperiments: (query: ExperimentListQuery = {}, signal?: AbortSignal) =>
    requestPage<ExperimentListItem>(`/api/experiments${queryString(query)}`, signal),
  // Run mode (dataset + a + b): starts a job. Compare mode (suite_a + suite_b): returns the
  // stored Comparison directly (201), because both suites are already finished.
  createExperiment: (body: ExperimentCreate) => post<JobAccepted>('/api/experiments', body),
  compareSuites: (body: { name: string; suite_a: string; suite_b: string }) =>
    post<Comparison>('/api/experiments', body),
  getExperiment: async (experimentId: string, signal?: AbortSignal) =>
    request<Comparison>(`/api/experiments/${seg(experimentId)}`, { signal }),

  failures: (query: FailureQuery = {}, signal?: AbortSignal) =>
    request<FailureAnalysis>(`/api/failures${queryString(query)}`, { signal }),

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
