import { expect, test as base, type APIRequestContext, type Page, type Request } from '@playwright/test'

/*
 * Shared helpers for the end-to-end specs. Every spec runs against the server started by
 * playwright.config.ts: the demo story seeded into a throwaway database. Ids are random per
 * seed, so specs look the demo data up by its labels and case ids through the API.
 */

export const ORIGIN = 'http://127.0.0.1:8799'

/** Required by the API's mutation guard on every state-changing request. */
export const CLIENT_HEADERS = { 'X-AgentScope-Client': '1' }

interface SuiteListItem {
  suite_id: string
  label: string
  agent_ref: string
  dataset_name: string
}

interface ExperimentListItem {
  experiment_id: string
  name: string
}

interface JobStatus {
  state: 'queued' | 'running' | 'succeeded' | 'failed'
  resource_id: string
  error: string | null
}

export async function demoSuiteId(request: APIRequestContext, agentRef: string): Promise<string> {
  const response = await request.get('/api/evaluations', { params: { limit: 200 } })
  expect(response.ok()).toBe(true)
  const suites = (await response.json()) as SuiteListItem[]
  const suite = suites.find((s) => s.label === `demo: ${agentRef}`)
  expect(suite, `demo evaluation of ${agentRef}`).toBeDefined()
  return suite!.suite_id
}

export async function demoExperimentId(request: APIRequestContext, name: string): Promise<string> {
  const response = await request.get('/api/experiments', { params: { limit: 200 } })
  expect(response.ok()).toBe(true)
  const experiments = (await response.json()) as ExperimentListItem[]
  const experiment = experiments.find((x) => x.name === name)
  expect(experiment, `demo comparison ${name}`).toBeDefined()
  return experiment!.experiment_id
}

/** The run id of one case of a demo evaluation. */
export async function caseRunId(request: APIRequestContext, suiteId: string, caseId: string): Promise<string> {
  const response = await request.get(`/api/evaluations/${suiteId}`)
  expect(response.ok()).toBe(true)
  const suite = (await response.json()) as { cases: { case_id: string; run_id: string | null }[] }
  const result = suite.cases.find((c) => c.case_id === caseId)
  expect(result?.run_id, `run of ${caseId}`).toBeTruthy()
  return result!.run_id!
}

/** Starts a run through the API (as the dashboard does) and waits for its job to finish. */
export async function createRun(request: APIRequestContext, body: Record<string, unknown>): Promise<string> {
  const accepted = await request.post('/api/runs', { data: body, headers: CLIENT_HEADERS })
  expect(accepted.status(), await accepted.text()).toBe(202)
  const { job_id: jobId } = (await accepted.json()) as { job_id: string }
  let job: JobStatus | null = null
  await expect
    .poll(
      async () => {
        job = (await (await request.get(`/api/jobs/${jobId}`)).json()) as JobStatus
        return job.state
      },
      { timeout: 30_000 },
    )
    .toMatch(/succeeded|failed/)
  const finished = job as JobStatus | null
  expect(finished?.state, finished?.error ?? '').toBe('succeeded')
  return finished!.resource_id
}

/** Waits until the routed view has rendered its data (no loading skeleton left). */
export async function settled(page: Page): Promise<void> {
  await expect(page.locator('#main h1').first()).toBeVisible()
  await expect(page.locator('#main [aria-busy="true"]')).toHaveCount(0)
  await expect(page.locator('#main .skeleton .bar')).toHaveCount(0)
}

/** Opens a page in the given colour scheme (the dashboard follows the system theme). */
export async function openThemed(page: Page, path: string, theme: 'light' | 'dark'): Promise<void> {
  await page.emulateMedia({ colorScheme: theme, reducedMotion: 'reduce' })
  await page.goto(path)
  await expect(page.locator('html')).toHaveAttribute('data-theme', theme)
  await settled(page)
}

function offOrigin(request: Request): boolean {
  const url = request.url()
  return !(url.startsWith(`${ORIGIN}/`) || url.startsWith('data:') || url.startsWith('blob:'))
}

/**
 * The page fixture also records every request the browser makes: the dashboard must talk
 * to its own origin only, on every screen, so any other destination fails the test.
 */
export const test = base.extend<{ offOriginRequests: string[] }>({
  offOriginRequests: [
    async ({ page }, use) => {
      const seen: string[] = []
      page.on('request', (request) => {
        if (offOrigin(request)) seen.push(request.url())
      })
      await use(seen)
      expect(seen, 'requests that left the AgentScope origin').toEqual([])
    },
    { auto: true },
  ],
})

export { expect }
