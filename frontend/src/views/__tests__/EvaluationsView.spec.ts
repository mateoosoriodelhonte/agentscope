import { flushPromises, type VueWrapper } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { agentsResponse, caseResult, datasetSummary, gate, job, suiteItem, suiteResult } from '@/__tests__/builders'
import { loadFixture } from '@/__tests__/fixtures'
import { calls, json, mockFetch, never, type Reply } from '@/__tests__/mockFetch'
import { elsewhere, mountAt } from '@/__tests__/router'
import { pageTrail } from '@/composables/usePageMeta'
import EvaluationCaseView from '../EvaluationCaseView.vue'
import EvaluationsView from '../EvaluationsView.vue'
import EvaluationView from '../EvaluationView.vue'

let wrapper: VueWrapper | null = null

const ROUTES = [
  { path: '/evaluations', component: EvaluationsView },
  { path: '/evaluations/:suiteId', component: EvaluationView, props: true },
  { path: '/evaluations/:suiteId/cases/:caseId', component: EvaluationCaseView, props: true },
  elsewhere,
]

async function open(path: string, routes: Record<string, Reply>) {
  const fetchMock = mockFetch(routes)
  const mounted = await mountAt(ROUTES, path)
  wrapper = mounted.page
  return { ...mounted, fetchMock }
}

afterEach(() => {
  wrapper?.unmount()
  wrapper = null
  vi.unstubAllGlobals()
  vi.useRealTimers()
})

const SUITES = [
  suiteItem({ label: 'Nightly v1' }),
  suiteItem({ suite_id: 'suite-v2', agent_ref: 'research_assistant@v2', passed: 9, gate_passed: true, deterministic: false, provider: 'ollama', model: 'llama3.2' }),
  suiteItem({ suite_id: 'suite-x', dataset_name: 'task-planner', agent_ref: 'task_planner@v1', gate_passed: null, status: 'running' }),
]

describe('EvaluationsView', () => {
  it('lists evaluations with k/n pass counts, gate and simulated-model badges', async () => {
    const { page } = await open('/evaluations', { 'GET /api/evaluations': SUITES })
    const rows = page.findAll('tbody tr')
    expect(rows).toHaveLength(3)
    expect(rows[0]!.text()).toContain('Nightly v1')
    expect(rows[0]!.text()).toContain('6/10')
    expect(rows[0]!.text()).toContain('gate failed')
    expect(rows[0]!.text()).toContain('simulated')
    expect(rows[1]!.text()).toContain('9/10')
    expect(rows[1]!.text()).toContain('gate passed')
    expect(rows[1]!.text()).not.toContain('simulated')
    expect(rows[2]!.text()).toContain('no gate')
    expect(rows[2]!.text()).toContain('running')
    expect(page.text()).not.toMatch(/\d+%/)
  })

  it('filters through the URL and says when nothing matches', async () => {
    const { page, router } = await open('/evaluations?agent=research_assistant@v2', { 'GET /api/evaluations': SUITES })
    expect(page.findAll('tbody tr')).toHaveLength(1)
    await page.findAll('select')[0]!.setValue('task-planner@1')
    await flushPromises()
    expect(router.currentRoute.value.query).toMatchObject({ dataset: 'task-planner@1', agent: 'research_assistant@v2' })
    expect(page.text()).toContain('No evaluations match these filters')
  })

  it('has loading, first-run and error states', async () => {
    let { page } = await open('/evaluations', { 'GET /api/evaluations': () => never() })
    expect(page.find('.skeleton').exists()).toBe(true)
    page.unmount()
    ;({ page } = await open('/evaluations', { 'GET /api/evaluations': [] }))
    expect(page.text()).toContain('No evaluations yet')
    page.unmount()
    ;({ page } = await open('/evaluations', { 'GET /api/evaluations': () => json({ detail: 'kaput' }, 500) }))
    expect(page.find('[role="alert"]').text()).toContain('kaput')
  })

  it('starts an evaluation of a preselected dataset with gate thresholds, then follows its job', async () => {
    const { page, router, fetchMock } = await open('/evaluations?new=research-assistant@1', {
      'GET /api/evaluations': SUITES,
      'GET /api/datasets': [datasetSummary({ name: 'other', version: '2', agent: null }), datasetSummary()],
      'GET /api/agents': agentsResponse(),
      'POST /api/evaluations': () => json({ job_id: 'job-e', kind: 'evaluation', resource_id: 'suite-new' }, 202),
    })
    const dialog = page.find('[role="dialog"]')
    const selects = dialog.findAll('select')
    expect((selects[0]!.element as HTMLSelectElement).value).toBe('research-assistant@1')
    expect((selects[1]!.element as HTMLSelectElement).value).toBe('research_assistant@v2')
    await dialog.find('input[type="checkbox"]').setValue(true)
    const inputs = page.find('[role="dialog"] .grid').findAll('input.input')
    await inputs[0]!.setValue('80')
    await page.find('[role="dialog"] .required input[value="policy_compliance"]').setValue(true)
    await page.find('[role="dialog"] form').trigger('submit')
    await flushPromises()
    expect(calls(fetchMock, 'POST', '/api/evaluations')[0]!.body).toEqual({
      agent_ref: 'research_assistant@v2',
      dataset: { name: 'research-assistant', version: '1' },
      label: '',
      thresholds: {
        min_pass_rate: 0.8,
        max_invalid_outputs: null,
        max_policy_violation_cases: null,
        max_unauthorized_tool_executions: 0,
        max_harness_errors: 0,
        required_checks: ['policy_compliance'],
      },
    })
    expect(router.currentRoute.value.fullPath).toBe('/evaluations/suite-new?job=job-e')
  })
})

describe('EvaluationView', () => {
  it('shows the gate checklist, metrics as k/n, the matrix and the simulated-model badge', async () => {
    const { page } = await open('/evaluations/suite-v1', { 'GET /api/evaluations/suite-v1': suiteResult() })
    expect(page.find('h1').text()).toBe('Nightly v1')
    expect(page.text()).toContain('Simulated model: demonstrates harness mechanics, not model behaviour')
    expect(page.findAll('.gate tbody tr')).toHaveLength(3)
    const metrics = page.find('.metrics').text()
    expect(metrics).toContain('Passed2/3')
    expect(metrics).toContain('Unauthorized attempts (simulated susceptibility)2')
    expect(metrics).toContain('Unauthorized executions (enforced by runtime permissions, must be 0)0')
    expect(metrics).toMatch(/Tokens\s*not reported/)
    expect(metrics).not.toMatch(/\d%/)
    expect(page.findAll('.matrix tbody tr')).toHaveLength(3)
    expect(page.find('.matrix tfoot').text()).toContain('2/3')
    expect(page.find('a[download]').attributes('href')).toBe('/api/evaluations/suite-v1/export')
  })

  it('shows only failing cases on request', async () => {
    const { page, router } = await open('/evaluations/suite-v1', { 'GET /api/evaluations/suite-v1': suiteResult() })
    await page.find('.section-head input[type="checkbox"]').setValue(true)
    await flushPromises()
    expect(router.currentRoute.value.query.failing).toBe('true')
    expect(page.findAll('.matrix tbody tr').map((r) => r.find('th').text())).toEqual(['failed: qa-2'])
  })

  it('follows the job of a running evaluation, then shows the result', async () => {
    let polls = 0
    const { page, router } = await open('/evaluations/suite-new?job=job-e', {
      'GET /api/evaluations/suite-new': () =>
        polls < 2 ? json({ detail: 'evaluation not found' }, 404) : suiteResult({ suite_id: 'suite-new' }),
      'GET /api/jobs/job-e': () => {
        polls += 1
        return job({ job_id: 'job-e', kind: 'evaluation', state: polls < 2 ? 'running' : 'succeeded', progress: polls < 2 ? 1 : 3, total: 3 })
      },
    })
    expect(page.find('[role="status"]').text()).toContain('Running: 1/3 cases evaluated')
    expect(page.find('[role="alert"]').exists()).toBe(false)
    await vi.waitFor(() => expect(router.currentRoute.value.query.job).toBeUndefined(), { timeout: 2000 })
    await flushPromises()
    expect(page.find('h1').text()).toBe('Nightly v1')
    expect(page.findAll('.matrix tbody tr')).toHaveLength(3)
  })

  it('keeps refreshing a running evaluation opened without a job until it finishes', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    let loads = 0
    const { page, fetchMock } = await open('/evaluations/suite-v1', {
      'GET /api/evaluations/suite-v1': () => {
        loads += 1
        return suiteResult({ status: loads < 3 ? 'running' : 'completed', cases: suiteResult().cases.slice(0, loads) })
      },
    })
    expect(page.find('[role="status"]').text()).toContain('Still running: 1 case recorded so far')
    await vi.advanceTimersByTimeAsync(2000)
    await flushPromises()
    expect(page.find('[role="status"]').text()).toContain('2 cases recorded so far')
    await vi.advanceTimersByTimeAsync(2000)
    await flushPromises()
    expect(page.find('[role="status"]').exists()).toBe(false)
    await vi.advanceTimersByTimeAsync(10_000)
    expect(calls(fetchMock, 'GET', '/api/evaluations/suite-v1')).toHaveLength(3)
  })

  it('reports a missing evaluation', async () => {
    const { page } = await open('/evaluations/nope', {})
    expect(page.find('[role="alert"]').text()).toContain('Could not load this evaluation')
  })

  it('reports an unauthorized execution prominently and labels attempts honestly for real models', async () => {
    const suite = suiteResult({ metrics: { ...suiteResult().metrics, unauthorized_tool_executions: 1 } })
    suite.environment = { ...suite.environment, deterministic: false }
    suite.gate = gate(true)
    const { page } = await open('/evaluations/suite-v1', { 'GET /api/evaluations/suite-v1': suite })
    expect(page.find('.metric-strong').classes()).toContain('bad')
    expect(page.find('.metrics').text()).not.toContain('simulated susceptibility')
    expect(page.text()).not.toContain('Simulated model: demonstrates')
  })
})

describe('EvaluationCaseView', () => {
  it('opens the case trace under a suite › case breadcrumb and passes ?check= through', async () => {
    const detail = loadFixture('run-v1-strict')
    const suite = suiteResult({ cases: [caseResult('qa-1', ['answer_correctness'], { run_id: detail.trace.run_id })] })
    const { page, router } = await open('/evaluations/suite-v1/cases/qa-1?check=answer_correctness', {
      'GET /api/evaluations/suite-v1': suite,
      [`GET /api/runs/${detail.trace.run_id}`]: detail,
    })
    await flushPromises()
    expect(pageTrail.value).toEqual([
      { label: 'Nightly v1', to: '/evaluations/suite-v1' },
      { label: 'qa-1', to: '/evaluations/suite-v1/cases/qa-1' },
    ])
    expect(page.find('.run-header h1').text()).toBe(detail.trace.task)
    const evidence = detail.evaluation!.checks.find((c) => c.name === 'answer_correctness')!.evidence[0]!.span_id
    expect(router.currentRoute.value.query.span).toBe(evidence)
  })

  it('shows the recorded checks of a case that has no trace', async () => {
    const suite = suiteResult({ cases: [caseResult('qa-1', ['task_completion'], { run_id: null, harness_error: 'tool registry crashed' })] })
    const { page } = await open('/evaluations/suite-v1/cases/qa-1', { 'GET /api/evaluations/suite-v1': suite })
    expect(page.find('h1').text()).toBe('Case qa-1')
    expect(page.text()).toContain('the harness could not run it (tool registry crashed)')
    expect(page.findAll('.checks li')).toHaveLength(10)
  })
})
