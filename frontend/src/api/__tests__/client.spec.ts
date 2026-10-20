import { afterEach, beforeEach, describe, expect, it, vi, type Mock } from 'vitest'
import type { JobStatus } from '../types'
import { api, ApiError, JobFailedError, pollJob } from '../client'

let fetchMock: Mock<(input: string, init: RequestInit) => Promise<Response>>

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })

beforeEach(() => {
  fetchMock = vi.fn(async () => json({}))
  vi.stubGlobal('fetch', fetchMock)
})

afterEach(() => {
  vi.unstubAllGlobals()
  vi.useRealTimers()
})

function lastCall(): { url: string; init: RequestInit; headers: Headers } {
  const [url, init] = fetchMock.mock.calls[fetchMock.mock.calls.length - 1]!
  return { url, init, headers: new Headers(init.headers) }
}

describe('api reads', () => {
  it.each([
    ['health', () => api.health(), '/api/health'],
    ['overview', () => api.overview(), '/api/overview'],
    ['agents', () => api.agents(), '/api/agents'],
    ['providers', () => api.providers(), '/api/providers'],
    ['job', () => api.job('j 1'), '/api/jobs/j%201'],
    ['getRun', () => api.getRun('r/1'), '/api/runs/r%2F1'],
    ['listDatasets', () => api.listDatasets(), '/api/datasets'],
    ['getDataset', () => api.getDataset('qa', '2'), '/api/datasets/qa/2'],
    ['listEvaluations', () => api.listEvaluations(), '/api/evaluations'],
    ['getEvaluation', () => api.getEvaluation('s1'), '/api/evaluations/s1'],
    ['listExperiments', () => api.listExperiments(), '/api/experiments'],
    ['getExperiment', () => api.getExperiment('e1'), '/api/experiments/e1'],
    ['listAblations', () => api.listAblations(), '/api/ablations'],
    ['getAblation', () => api.getAblation('a/1'), '/api/ablations/a%2F1'],
    ['failures', () => api.failures(), '/api/failures'],
    ['settings', () => api.settings(), '/api/settings'],
  ])('%s → GET %s without mutation headers', async (_name, call, url) => {
    await call()
    const { url: requested, init, headers } = lastCall()
    expect(requested).toBe(url)
    expect(init.method).toBeUndefined()
    expect(init.credentials).toBe('same-origin')
    expect(headers.get('X-AgentScope-Client')).toBeNull()
    expect(headers.get('Content-Type')).toBeNull()
  })

  it('builds run-list filters into the query string, omitting unset ones', async () => {
    await api.listRuns({
      status: 'failed',
      agent: 'research_assistant',
      agent_version: 'v2',
      has_faults: true,
      has_blocked_calls: false,
      verdict: 'none',
      limit: 50,
      offset: 100,
      case_id: undefined,
    })
    expect(lastCall().url).toBe(
      '/api/runs?status=failed&agent=research_assistant&agent_version=v2&has_faults=true&has_blocked_calls=false&verdict=none&limit=50&offset=100',
    )
    await api.listRuns()
    expect(lastCall().url).toBe('/api/runs')
  })

  it('builds export links for downloads', () => {
    expect(api.runExportUrl('r1')).toBe('/api/runs/r1/export')
    expect(api.datasetExportUrl('qa', '1.0')).toBe('/api/datasets/qa/1.0/export')
    expect(api.evaluationExportUrl('s1')).toBe('/api/evaluations/s1/export')
  })

  it('refuses dot segments that would climb out of the route', async () => {
    await expect(api.getRun('..')).rejects.toBeInstanceOf(ApiError)
    await expect(api.getDataset('qa', '.')).rejects.toMatchObject({ status: 400 })
    await expect(api.job('')).rejects.toBeInstanceOf(ApiError)
    expect(() => api.datasetExportUrl('..', '1')).toThrow(ApiError)
    expect(fetchMock).not.toHaveBeenCalled()
    // Dots inside a segment are fine.
    await api.getDataset('qa', '1.0')
    expect(lastCall().url).toBe('/api/datasets/qa/1.0')
  })

  it('passes the abort signal through', async () => {
    const controller = new AbortController()
    await api.getRun('r1', controller.signal)
    expect(lastCall().init.signal).toBe(controller.signal)
  })
})

describe('api mutations', () => {
  it.each([
    ['createRun', () => api.createRun({ agent_ref: 'research_assistant@v2', task: 'q' }), '/api/runs', { agent_ref: 'research_assistant@v2', task: 'q' }],
    ['importDataset', () => api.importDataset('{"schema_version": 1, "name": "qa"}'), '/api/datasets', { schema_version: 1, name: 'qa' }],
    ['validateDataset', () => api.validateDataset('{"name": "qa"}'), '/api/datasets/validate', { name: 'qa' }],
    ['createEvaluation', () => api.createEvaluation({ agent_ref: 'a@v1', dataset: { name: 'qa', version: '1' } }), '/api/evaluations', { agent_ref: 'a@v1', dataset: { name: 'qa', version: '1' } }],
    ['createExperiment', () => api.createExperiment({ name: 'x', suite_a: 's1', suite_b: 's2' }), '/api/experiments', { name: 'x', suite_a: 's1', suite_b: 's2' }],
    ['createAblation', () => api.createAblation({ experiment_id: 'e1' }), '/api/ablations', { experiment_id: 'e1' }],
    ['createAblation (run mode)', () => api.createAblation({ dataset: { name: 'qa', version: '1' }, a: { agent_ref: 'a@v1' }, b: { agent_ref: 'a@v2' } }), '/api/ablations', { dataset: { name: 'qa', version: '1' }, a: { agent_ref: 'a@v1' }, b: { agent_ref: 'a@v2' } }],
    ['purge', () => api.purge({ confirm: 'PURGE', scope: 'runs' }), '/api/settings/purge', { confirm: 'PURGE', scope: 'runs' }],
    ['seedDemo', () => api.seedDemo(), '/api/demo/seed', {}],
  ])('%s → POST %s as JSON with the client header', async (_name, call, url, body) => {
    await call()
    const { url: requested, init, headers } = lastCall()
    expect(requested).toBe(url)
    expect(init.method).toBe('POST')
    expect(headers.get('Content-Type')).toBe('application/json')
    expect(headers.get('X-AgentScope-Client')).toBe('1')
    expect(JSON.parse(String(init.body))).toEqual(body)
  })
})

describe('dataset documents', () => {
  it('sends the text exactly as written, so the server applies its strict JSON rules', async () => {
    // JSON.parse would keep the last duplicate key and turn 1e400 into Infinity (then null).
    const text = '{"name": "qa", "cases": [{"forbidden_tools": ["write_note"], "forbidden_tools": [], "tolerance": 1e400}]}\n'
    await api.validateDataset(text)
    expect(lastCall().init.body).toBe(text)
    await api.importDataset(text)
    expect(lastCall().init.body).toBe(text)
  })
})

describe('paged lists', () => {
  const page = (body: unknown, total?: string) =>
    new Response(JSON.stringify(body), {
      headers: { 'Content-Type': 'application/json', ...(total === undefined ? {} : { 'X-Total-Count': total }) },
    })

  it('sends evaluation filters and paging, and reads the total from X-Total-Count', async () => {
    fetchMock.mockResolvedValueOnce(page([{ suite_id: 's51' }], '55'))
    const result = await api.listEvaluations({
      agent_ref: 'research_assistant@v2',
      dataset: 'research-assistant',
      status: 'completed',
      limit: 50,
      offset: 50,
    })
    expect(lastCall().url).toBe(
      '/api/evaluations?agent_ref=research_assistant%40v2&dataset=research-assistant&status=completed&limit=50&offset=50',
    )
    expect(result).toEqual({ items: [{ suite_id: 's51' }], total: 55 })
  })

  it('pages experiments', async () => {
    fetchMock.mockResolvedValueOnce(page([{ experiment_id: 'e1' }], '3'))
    expect(await api.listExperiments({ limit: 20, offset: 0 })).toEqual({ items: [{ experiment_id: 'e1' }], total: 3 })
    expect(lastCall().url).toBe('/api/experiments?limit=20&offset=0')
  })

  it('finds the ablations of one comparison, reading the total from X-Total-Count', async () => {
    fetchMock.mockResolvedValueOnce(page([{ ablation_id: 'a1' }], '2'))
    expect(await api.listAblations({ experiment_id: 'e 1', limit: 1 })).toEqual({ items: [{ ablation_id: 'a1' }], total: 2 })
    expect(lastCall().url).toBe('/api/ablations?experiment_id=e+1&limit=1')
  })

  it('accepts an {items, total} body, and says when the total is unknown', async () => {
    fetchMock.mockResolvedValueOnce(page({ items: [{ suite_id: 's1' }], total: 7 }))
    expect(await api.listEvaluations()).toEqual({ items: [{ suite_id: 's1' }], total: 7 })
    fetchMock.mockResolvedValueOnce(page([{ suite_id: 's1' }]))
    expect(await api.listEvaluations()).toEqual({ items: [{ suite_id: 's1' }], total: null })
    fetchMock.mockResolvedValueOnce(page([], 'many'))
    expect(await api.listExperiments()).toEqual({ items: [], total: null })
  })
})

describe('errors', () => {
  it('raises ApiError with the server detail', async () => {
    fetchMock.mockResolvedValueOnce(json({ detail: 'run not found' }, 404))
    const error = await api.getRun('missing').catch((e: unknown) => e)
    expect(error).toBeInstanceOf(ApiError)
    expect(error).toMatchObject({ status: 404, message: 'run not found' })
  })

  it('falls back to the status when the body is not JSON', async () => {
    fetchMock.mockResolvedValueOnce(new Response('<html>', { status: 502, statusText: 'Bad Gateway' }))
    await expect(api.health()).rejects.toMatchObject({ status: 502, message: 'Bad Gateway', body: null })
  })

  it('summarises FastAPI validation errors by location', async () => {
    const detail = [
      { loc: ['body', 'task'], msg: 'String should have at least 1 character', type: 'string_too_short' },
      { loc: ['body', 'faults', 0, 'kind'], msg: 'Input should be a fault kind', type: 'enum' },
    ]
    fetchMock.mockResolvedValueOnce(json({ detail }, 422))
    await expect(api.createRun({ agent_ref: 'a@v1', task: '' })).rejects.toMatchObject({
      status: 422,
      message: 'task: String should have at least 1 character; faults.0.kind: Input should be a fault kind',
    })
  })

  it('keeps the located issues of a 422, named from the request body', async () => {
    const issues = [
      { path: '/body/expected/values', message: 'List should have at most 32 items after validation, not 33', severity: 'error' },
      { path: '/cases/0/agent', message: 'agent research_assistant does not match', severity: 'warning' },
      { path: '/body', message: 'Field required' },
      { message: 42 },
    ]
    fetchMock.mockResolvedValueOnce(json({ detail: 'the request is invalid', issues }, 422))
    const error = await api.createRun({ agent_ref: 'a@v1', task: 'q' }).catch((e: unknown) => e)
    expect(error).toMatchObject({ status: 422, message: 'the request is invalid' })
    expect((error as ApiError).issues).toEqual([
      { path: '/expected/values', message: 'List should have at most 32 items after validation, not 33', severity: 'error' },
      { path: '/cases/0/agent', message: 'agent research_assistant does not match', severity: 'warning' },
      { path: '/', message: 'Field required', severity: 'error' },
    ])
  })

  it('has no issues when the body carries none', async () => {
    fetchMock.mockResolvedValueOnce(json({ detail: 'run not found' }, 404))
    const error = await api.getRun('missing').catch((e: unknown) => e)
    expect((error as ApiError).issues).toEqual([])
  })

  it('keeps a structured error body for the caller', async () => {
    const body = { detail: { message: 'purge again', result: { deleted: { runs: 2 } } } }
    fetchMock.mockResolvedValueOnce(json(body, 409))
    const error = await api.purge({ confirm: 'PURGE' }).catch((e: unknown) => e)
    expect(error).toMatchObject({ status: 409, message: 'purge again', body })
  })
})

describe('failure analysis filters', () => {
  it('sends only the filters that are set', async () => {
    await api.failures({ agent_ref: 'research_assistant@v2', suite_id: undefined })
    expect(lastCall().url).toBe('/api/failures?agent_ref=research_assistant%40v2')
  })
})

describe('pollJob', () => {
  const job = (state: JobStatus['state'], extra: Partial<JobStatus> = {}): JobStatus => ({
    job_id: 'j1',
    kind: 'evaluation',
    state,
    resource_id: 's1',
    created_at: '2026-10-16T00:00:00Z',
    finished_at: null,
    progress: 0,
    total: 0,
    error: null,
    ...extra,
  })

  it('polls with exponential backoff until the job succeeds', async () => {
    vi.useFakeTimers()
    fetchMock
      .mockResolvedValueOnce(json(job('queued')))
      .mockResolvedValueOnce(json(job('running', { progress: 1, total: 3 })))
      .mockResolvedValueOnce(json(job('succeeded', { progress: 3, total: 3 })))
    const progress: number[] = []
    const done = pollJob('j1', { onProgress: (j) => progress.push(j.progress) })
    await vi.advanceTimersByTimeAsync(0)
    expect(fetchMock).toHaveBeenCalledTimes(1)
    await vi.advanceTimersByTimeAsync(249)
    expect(fetchMock).toHaveBeenCalledTimes(1)
    await vi.advanceTimersByTimeAsync(1)
    expect(fetchMock).toHaveBeenCalledTimes(2)
    await vi.advanceTimersByTimeAsync(399) // 250 × 1.6 = 400
    expect(fetchMock).toHaveBeenCalledTimes(2)
    await vi.advanceTimersByTimeAsync(1)
    await expect(done).resolves.toMatchObject({ state: 'succeeded' })
    expect(progress).toEqual([0, 1, 3])
    expect(lastCall().url).toBe('/api/jobs/j1')
  })

  it('caps the delay', async () => {
    vi.useFakeTimers()
    fetchMock.mockImplementation(async () => json(job('running')))
    const controller = new AbortController()
    const done = pollJob('j1', { signal: controller.signal, initialDelayMs: 1000, maxDelayMs: 1500 })
    done.catch(() => undefined)
    await vi.advanceTimersByTimeAsync(1000 + 1500 + 1500)
    expect(fetchMock).toHaveBeenCalledTimes(4)
    controller.abort()
  })

  it('rejects with JobFailedError when the job fails', async () => {
    fetchMock.mockResolvedValueOnce(json(job('failed', { error: 'dataset not found' })))
    const error = await pollJob('j1').catch((e: unknown) => e)
    expect(error).toBeInstanceOf(JobFailedError)
    expect(error).toMatchObject({ message: 'dataset not found' })
  })

  it('stops when aborted', async () => {
    vi.useFakeTimers()
    fetchMock.mockImplementation(async () => json(job('running')))
    const controller = new AbortController()
    const done = pollJob('j1', { signal: controller.signal })
    const settled = done.catch((e: unknown) => e)
    await vi.advanceTimersByTimeAsync(0)
    controller.abort()
    const error = await settled
    expect((error as DOMException).name).toBe('AbortError')
    await vi.advanceTimersByTimeAsync(10_000)
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })
})
