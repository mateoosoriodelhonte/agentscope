import type { RunDetail, RunList } from './types'

export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message)
  }
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers)
  if (init.method && init.method !== 'GET') {
    headers.set('Content-Type', 'application/json')
    // Required by the API's mutation guard; forces a CORS preflight cross-origin.
    headers.set('X-AgentScope-Client', '1')
  }
  const response = await fetch(path, { ...init, headers, credentials: 'same-origin' })
  if (!response.ok) {
    let detail = response.statusText
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

export const api = {
  listRuns: (limit = 50) => request<RunList>(`/api/runs?limit=${limit}`),
  getRun: (runId: string) => request<RunDetail>(`/api/runs/${encodeURIComponent(runId)}`),
}
