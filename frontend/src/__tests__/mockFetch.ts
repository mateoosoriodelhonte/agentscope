import { vi, type Mock } from 'vitest'

export type Reply = unknown | ((url: URL, init: RequestInit) => unknown)

export type FetchMock = Mock<(input: string, init?: RequestInit) => Promise<Response>>

export function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}

/** One page of a list endpoint, with the total in X-Total-Count as the API sends it. */
export function paged(items: unknown[], total: number = items.length): Response {
  return new Response(JSON.stringify(items), {
    headers: { 'Content-Type': 'application/json', 'X-Total-Count': String(total) },
  })
}

/** Replies with the slice of `all` that a request's limit and offset ask for. */
export function pageOf(all: unknown[]): (url: URL) => Response {
  return (url) => {
    const offset = Number(url.searchParams.get('offset') ?? 0)
    const limit = Number(url.searchParams.get('limit') ?? 50)
    return paged(all.slice(offset, offset + limit), all.length)
  }
}

/**
 * Stubs global fetch with a route table keyed by "METHOD /path" (or "METHOD /path?query" for
 * an exact query). A reply is a JSON body, a Response, an Error to throw (a TypeError is what
 * fetch throws when nothing answers), or a function of the request returning any of those.
 * Unknown routes answer 404, as the API would.
 */
export function mockFetch(routes: Record<string, Reply>): FetchMock {
  const fetchMock: FetchMock = vi.fn(async (input: string, init: RequestInit = {}) => {
    const url = new URL(input, 'http://127.0.0.1')
    const key = `${init.method ?? 'GET'} ${url.pathname}`
    const reply = routes[`${key}${url.search}`] ?? routes[key]
    if (reply === undefined) return json({ detail: 'Not Found' }, 404)
    const value: unknown = typeof reply === 'function' ? await reply(url, init) : reply
    if (value instanceof Response) return value
    if (value instanceof Error) throw value
    return json(value)
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

/** A reply that never arrives, for loading states. */
export function never(): Promise<never> {
  return new Promise<never>(() => undefined)
}

/** Requests made to one route, with their parsed JSON bodies. */
export function calls(fetchMock: FetchMock, method: string, path: string): { url: URL; body: unknown }[] {
  return fetchMock.mock.calls
    .map(([input, init]) => ({ url: new URL(input, 'http://127.0.0.1'), init: init ?? {} }))
    .filter(({ url, init }) => (init.method ?? 'GET') === method && url.pathname === path)
    .map(({ url, init }) => ({ url, body: init.body ? (JSON.parse(String(init.body)) as unknown) : undefined }))
}
