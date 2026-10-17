import { flushPromises, type VueWrapper } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { agentsResponse, comparison, datasetSummary, experimentItem, job, runSummary, suiteItem, suiteResult } from '@/__tests__/builders'
import { loadFixture } from '@/__tests__/fixtures'
import { calls, json, mockFetch, never, pageOf, paged, type Reply } from '@/__tests__/mockFetch'
import { elsewhere, mountAt } from '@/__tests__/router'
import CompareRunsView from '../CompareRunsView.vue'
import CompareView from '../CompareView.vue'

let wrapper: VueWrapper | null = null

const ROUTES = [
  { path: '/compare', component: CompareView },
  { path: '/compare/runs', component: CompareRunsView },
  elsewhere,
]

const BASE: Record<string, Reply> = {
  'GET /api/evaluations': [suiteItem(), suiteItem({ suite_id: 'suite-v2', agent_ref: 'research_assistant@v2', gate_passed: true })],
  'GET /api/experiments': [],
  'GET /api/datasets': [datasetSummary()],
  'GET /api/agents': agentsResponse(),
}

async function open(path: string, routes: Record<string, Reply> = {}) {
  const fetchMock = mockFetch({ ...BASE, ...routes })
  const mounted = await mountAt(ROUTES, path)
  wrapper = mounted.page
  return { ...mounted, fetchMock }
}

afterEach(() => {
  wrapper?.unmount()
  wrapper = null
  vi.unstubAllGlobals()
})

describe('CompareView', () => {
  it('shows the dataset, the confounded label, metric deltas and per-check bars', async () => {
    const { page } = await open('/compare?experiment=exp-1', { 'GET /api/experiments/exp-1': comparison() })
    expect(page.find('h1').text()).toBe('research_assistant@v1 vs research_assistant@v2')
    expect(page.find('.facts').text()).toContain('research-assistant@1')
    expect(page.find('.facts').text()).toContain(`sha256:${'d'.repeat(64)}`)
    expect(page.text()).toContain('Simulated model: demonstrates harness mechanics')
    const confounded = page.find('.confounded')
    expect(confounded.text()).toContain('Confounded: attribution not possible')
    expect(confounded.text()).toContain('3 dimensions changed at once (prompt, graph, retry policy)')
    expect(page.findAll('.config-diff tr.group').map((g) => g.text())).toEqual(['Prompt', 'Graph', 'Retry policy'])
    expect(page.find('tr[data-metric="pass_rate"]').text()).toContain('+30 pp')
    expect(page.find('tr[data-metric="check:policy_compliance"]').exists()).toBe(false)
    expect(page.find('.check-bars').text()).toContain('policy_compliance')
    expect(page.find('.check-bars').text()).toContain('n=4')
  })

  it('orders flips Regressed, Fixed, Still failing (changed), Still failing (same), Still passing (collapsed)', async () => {
    const { page } = await open('/compare?experiment=exp-1', { 'GET /api/experiments/exp-1': comparison() })
    const groups = page.findAll('details.group')
    expect(groups.map((g) => g.find('.title').text())).toEqual([
      'Regressed',
      'Fixed',
      'Still failing (changed)',
      'Still failing (same)',
      'Still passing',
    ])
    expect(groups.map((g) => g.attributes('open') !== undefined)).toEqual([true, true, true, true, false])
    expect(page.find('.apart').text()).toContain('qa-edited')
    expect(page.find('.apart').text()).toContain('left out of every flip count')
  })

  it('opens the trace diff of a selected flip', async () => {
    const v1 = loadFixture('run-v1')
    const v2 = loadFixture('run-v2')
    const { page, router } = await open('/compare?experiment=exp-1', {
      'GET /api/experiments/exp-1': comparison(),
      'GET /api/runs/a-qa-broken': v1,
      'GET /api/runs/b-qa-broken': v2,
    })
    await page.find('details.regression button').trigger('click')
    await flushPromises()
    expect(router.currentRoute.value.query.case).toBe('qa-broken')
    expect(page.find('#diff-title').text()).toContain('qa-broken')
    expect(page.find('.trace-diff .callout.warn').text()).toContain('First divergence')
    expect(page.findAll('a').find((a) => a.text() === 'Open on its own page')!.attributes('href')).toBe(
      '/compare/runs?a=a-qa-broken&b=b-qa-broken',
    )
  })

  it('explains a deep-linked case that has no run on one side instead of loading forever', async () => {
    const data = comparison()
    const target = data.cases.find((c) => c.change === 'regression')!
    target.b_run_id = null
    const { page, fetchMock } = await open(`/compare?experiment=exp-1&case=${target.case_id}`, {
      'GET /api/experiments/exp-1': data,
    })
    expect(page.find('#trace-diff').text()).toContain('side B has no recorded run')
    expect(page.find('#trace-diff .skeleton').exists()).toBe(false)
    expect(calls(fetchMock, 'GET', `/api/runs/${target.a_run_id}`)).toHaveLength(0)
  })

  it('warns when the dataset content differs and when flips may be noise', async () => {
    const data = comparison({ dataset_mismatch: true, deterministic: false })
    data.b = { ...data.b, dataset_hash: `sha256:${'9'.repeat(64)}` }
    const { page } = await open('/compare?experiment=exp-1', { 'GET /api/experiments/exp-1': data })
    expect(page.text()).toContain('Dataset content differs.')
    expect(page.text()).toContain('only shared, unchanged cases are compared')
    expect(page.text()).toContain('flips may be noise')
  })

  it('computes a comparison of two suites named in the URL once, then keeps its id', async () => {
    const { router, fetchMock } = await open('/compare?a=suite-v1&b=suite-v2', {
      'POST /api/experiments': () => json(comparison({ experiment_id: 'exp-1' }), 201),
      'GET /api/experiments/exp-1': comparison(),
    })
    await flushPromises()
    expect(calls(fetchMock, 'POST', '/api/experiments').map((c) => c.body)).toEqual([
      { name: 'research_assistant@v1 vs research_assistant@v2', suite_a: 'suite-v1', suite_b: 'suite-v2' },
    ])
    expect(router.currentRoute.value.query).toMatchObject({ a: 'suite-v1', b: 'suite-v2', experiment: 'exp-1' })
    expect(wrapper!.find('h1').text()).toBe('research_assistant@v1 vs research_assistant@v2')
  })

  it('reuses a stored comparison of the same two suites instead of storing another', async () => {
    const { router, fetchMock } = await open('/compare?a=suite-v1&b=suite-v2', {
      'GET /api/experiments': paged([experimentItem({ experiment_id: 'exp-other', b_ref: 'research_assistant@v3' }), experimentItem()]),
      'GET /api/experiments/exp-1': comparison(),
      'POST /api/experiments': () => json(comparison({ experiment_id: 'exp-new' }), 201),
    })
    await flushPromises()
    expect(calls(fetchMock, 'POST', '/api/experiments')).toHaveLength(0)
    // Only the candidate with the same agent versions is checked, by its suite ids.
    expect(calls(fetchMock, 'GET', '/api/experiments/exp-other')).toHaveLength(0)
    expect(router.currentRoute.value.query).toMatchObject({ a: 'suite-v1', b: 'suite-v2', experiment: 'exp-1' })
    expect(wrapper!.find('h1').text()).toBe('research_assistant@v1 vs research_assistant@v2')
  })

  it('computes a new comparison when the stored one compared other suites of the same versions', async () => {
    const older = comparison()
    older.a = { ...older.a, suite_id: 'suite-v1-old' }
    const { router, fetchMock } = await open('/compare?a=suite-v1&b=suite-v2', {
      'GET /api/experiments': paged([experimentItem()]),
      'GET /api/experiments/exp-1': older,
      'POST /api/experiments': () => json(comparison({ experiment_id: 'exp-new' }), 201),
      'GET /api/experiments/exp-new': comparison({ experiment_id: 'exp-new' }),
    })
    await flushPromises()
    expect(calls(fetchMock, 'POST', '/api/experiments')).toHaveLength(1)
    expect(router.currentRoute.value.query.experiment).toBe('exp-new')
  })

  it('lists finished suites from the server, narrowed by dataset, and says when the list is cut', async () => {
    const many = Array.from({ length: 200 }, (_, i) => suiteItem({ suite_id: `s${i}` }))
    const planner = [
      suiteItem({ suite_id: 'tp-1', dataset_name: 'task-planner', agent_ref: 'task_planner@v1' }),
      suiteItem({ suite_id: 'tp-2', dataset_name: 'task-planner', agent_ref: 'task_planner@v2' }),
    ]
    const { page, router, fetchMock } = await open('/compare', {
      'GET /api/evaluations': (url: URL) => (url.searchParams.get('dataset') === 'task-planner' ? paged(planner) : paged(many, 260)),
      'GET /api/datasets': [datasetSummary(), datasetSummary({ name: 'task-planner', agent: 'task_planner' })],
    })
    expect(calls(fetchMock, 'GET', '/api/evaluations')[0]!.url.search).toBe('?status=completed&limit=200')
    const picker = () => page.find('[aria-labelledby="pick-suites-title"]')
    expect(picker().text()).toContain('the newest 200 of 260 finished evaluations')
    await picker().find('select').setValue('task-planner')
    await flushPromises()
    expect(router.currentRoute.value.query.dataset).toBe('task-planner')
    expect(calls(fetchMock, 'GET', '/api/evaluations').pop()!.url.search).toBe('?status=completed&dataset=task-planner&limit=200')
    const options = picker().findAll('select')[1]!.findAll('option').map((o) => o.attributes('value'))
    expect(options).toEqual(['', 'tp-1', 'tp-2'])
    expect(picker().text()).not.toContain('the newest')
  })

  it('keeps a suite named in the URL pickable when it is older than the listed ones', async () => {
    const { page } = await open('/compare?a=suite-old', {
      'GET /api/evaluations/suite-old': suiteResult({ suite_id: 'suite-old', label: 'Last spring' }),
    })
    await flushPromises()
    const a = page.findAll('[aria-labelledby="pick-suites-title"] select')[1]!
    expect((a.element as HTMLSelectElement).value).toBe('suite-old')
    expect(a.find('option[value="suite-old"]').text()).toContain('research_assistant@v1 · research-assistant@1 · 2/3')
  })

  it('pages through stored comparisons', async () => {
    const all = Array.from({ length: 25 }, (_, i) => experimentItem({ experiment_id: `e${i}`, name: `Comparison ${i}` }))
    const { page, router, fetchMock } = await open('/compare', { 'GET /api/experiments': pageOf(all) })
    expect(calls(fetchMock, 'GET', '/api/experiments')[0]!.url.search).toBe('?limit=20&offset=0')
    const recent = () => page.find('[aria-labelledby="recent-title"]')
    expect(recent().findAll('tbody tr')).toHaveLength(20)
    expect(recent().find('.pager').text()).toContain('1–20 of 25')
    await recent().findAll('.pager button').find((b) => b.text() === 'Next')!.trigger('click')
    await flushPromises()
    expect(router.currentRoute.value.query.page).toBe('2')
    expect(recent().findAll('tbody tr').map((r) => r.find('th a').text())).toEqual([
      'Comparison 20',
      'Comparison 21',
      'Comparison 22',
      'Comparison 23',
      'Comparison 24',
    ])
  })

  it('picks two suites, warning before comparing different dataset content', async () => {
    const { page, router } = await open('/compare', {
      'GET /api/evaluations': [suiteItem(), suiteItem({ suite_id: 'suite-other', dataset_hash: 'sha256:other' })],
      'POST /api/experiments': () => never(),
    })
    const [, a, b] = page.findAll('[aria-labelledby="pick-suites-title"] select')
    await a!.setValue('suite-v1')
    await b!.setValue('suite-other')
    expect(page.text()).toContain('These evaluations ran different dataset content')
    await page.findAll('button').find((x) => x.text() === 'Compare')!.trigger('click')
    await flushPromises()
    expect(router.currentRoute.value.query).toMatchObject({ a: 'suite-v1', b: 'suite-other' })
    expect(page.find('[role="status"]').text()).toContain('Computing the comparison')
  })

  it('runs an experiment from a dataset and two agent versions', async () => {
    const { page, router, fetchMock } = await open('/compare', {
      'POST /api/experiments': () => json({ job_id: 'job-r', kind: 'experiment', resource_id: 'exp-2' }, 202),
      'GET /api/jobs/job-r': job({ job_id: 'job-r', kind: 'experiment', resource_id: 'exp-2', progress: 20, total: 20 }),
      'GET /api/experiments/exp-2': comparison({ experiment_id: 'exp-2' }),
    })
    const selects = page.findAll('[aria-labelledby="run-experiment-title"] select')
    await selects[0]!.setValue('research-assistant@1')
    await selects[1]!.setValue('research_assistant@v1')
    await selects[2]!.setValue('research_assistant@v2')
    await page.findAll('button').find((x) => x.text() === 'Run both and compare')!.trigger('click')
    await flushPromises()
    expect(calls(fetchMock, 'POST', '/api/experiments')[0]!.body).toEqual({
      name: 'research_assistant@v1 vs research_assistant@v2',
      dataset: { name: 'research-assistant', version: '1' },
      a: { agent_ref: 'research_assistant@v1' },
      b: { agent_ref: 'research_assistant@v2' },
    })
    expect(router.currentRoute.value.query.experiment).toBe('exp-2')
  })

  it('says why the server refused to run an experiment, issue by issue', async () => {
    const { page } = await open('/compare', {
      'POST /api/experiments': () =>
        json(
          {
            detail: 'dataset research-assistant@1 cannot be evaluated with this agent',
            issues: [{ path: '/cases/0/agent', message: 'does not match task_planner', severity: 'error' }],
          },
          422,
        ),
    })
    const selects = page.findAll('[aria-labelledby="run-experiment-title"] select')
    await selects[0]!.setValue('research-assistant@1')
    await selects[1]!.setValue('research_assistant@v1')
    await selects[2]!.setValue('research_assistant@v2')
    await page.findAll('button').find((x) => x.text() === 'Run both and compare')!.trigger('click')
    await flushPromises()
    const alert = page.find('[role="alert"]')
    expect(alert.text()).toContain('Could not compare: dataset research-assistant@1 cannot be evaluated with this agent')
    expect(alert.findAll('li').map((li) => li.text())).toEqual(['/cases/0/agent does not match task_planner'])
  })

  it('shows a filled-in example when there are fewer than two evaluations', async () => {
    const { page } = await open('/compare', { 'GET /api/evaluations': [suiteItem()] })
    expect(page.find('.example').text()).toContain('agentscope eval --dataset research-assistant --agent research_assistant@v1')
  })

  it('reports a comparison that cannot be loaded', async () => {
    const { page } = await open('/compare?experiment=gone')
    expect(page.find('[role="alert"]').text()).toContain('Could not load this comparison')
  })
})

describe('CompareRunsView', () => {
  it('diffs two runs named in the URL', async () => {
    const v1 = loadFixture('run-v1')
    const v2 = loadFixture('run-v2')
    const { page } = await open(`/compare/runs?a=${v1.trace.run_id}&b=${v2.trace.run_id}`, {
      [`GET /api/runs/${v1.trace.run_id}`]: v1,
      [`GET /api/runs/${v2.trace.run_id}`]: v2,
    })
    expect(page.find('.subtitle').text()).toContain('research_assistant@v1')
    expect(page.findAll('.trace-diff tbody tr')).toHaveLength(4)
  })

  it('suggests recent runs by their full ids', async () => {
    const { page, fetchMock } = await open('/compare/runs', {
      'GET /api/runs': { items: [runSummary({ run_id: '6e2fdf5d-ec2f-4201-bfc2-cabeb227ff3e' })], total: 1 },
    })
    expect(calls(fetchMock, 'GET', '/api/runs')[0]!.url.search).toBe('?limit=50')
    const list = page.find('datalist')
    expect(list.findAll('option').map((o) => o.attributes('value'))).toEqual(['6e2fdf5d-ec2f-4201-bfc2-cabeb227ff3e'])
    for (const input of page.findAll('form.pick input')) expect(input.attributes('list')).toBe(list.attributes('id'))
  })

  it('explains that a shortened run id is not enough', async () => {
    const { page } = await open('/compare/runs?a=6e2fdf5d&b=7f3a0e6e')
    expect(page.find('[role="alert"]').text()).toContain('Could not load the two runs')
    expect(page.find('.short-ids').text()).toContain('full run id')
  })

  it('asks for two run ids', async () => {
    const { page, router } = await open('/compare/runs')
    expect(page.text()).toContain('Enter two full run ids')
    const inputs = page.findAll('input')
    await inputs[0]!.setValue('r1')
    await inputs[1]!.setValue('r2')
    await page.find('form').trigger('submit')
    await flushPromises()
    expect(router.currentRoute.value.query).toEqual({ a: 'r1', b: 'r2' })
  })
})
