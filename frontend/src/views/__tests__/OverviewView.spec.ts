import { flushPromises, type VueWrapper } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { job, overview, suiteItem } from '@/__tests__/builders'
import { calls, json, mockFetch, never, type Reply } from '@/__tests__/mockFetch'
import { elsewhere, mountAt } from '@/__tests__/router'
import { newRun } from '@/composables/newRun'
import OverviewView from '../OverviewView.vue'

let wrapper: VueWrapper | null = null

async function open(reply: Reply, query = '', extra: Record<string, Reply> = {}) {
  const fetchMock = mockFetch({ 'GET /api/overview': reply, ...extra })
  const mounted = await mountAt([{ path: '/overview', component: OverviewView }, elsewhere], `/overview${query}`)
  wrapper = mounted.page
  return { ...mounted, fetchMock }
}

const EMPTY = overview({
  counts: { runs: 0, suites: 0, datasets: 0, experiments: 0 },
  recent_runs: [],
  recent_suites: [],
  pass_rate_series: [],
  latest_gate: null,
})

beforeEach(() => window.localStorage.clear())
afterEach(() => {
  wrapper?.unmount()
  wrapper = null
  vi.unstubAllGlobals()
})

describe('OverviewView', () => {
  it('shows the heading and a delayed skeleton while loading', async () => {
    const { page } = await open(() => never())
    expect(page.find('h1').text()).toBe('Overview')
    expect(page.find('.skeleton').exists()).toBe(true)
    expect(page.find('[role="alert"]').exists()).toBe(false)
  })

  it('walks a first run through the demo story and seeds it on request', async () => {
    let seeded = false
    const { page, fetchMock } = await open(() => (seeded ? overview() : EMPTY), '', {
      'POST /api/demo/seed': () => {
        seeded = true
        return json({ job_id: 'seed-1', kind: 'demo_seed', resource_id: 'demo' }, 202)
      },
      'GET /api/jobs/seed-1': job({ job_id: 'seed-1', kind: 'demo_seed', progress: 4, total: 4 }),
    })
    expect(page.text()).toContain('Nothing recorded yet')
    const card = page.find('.start')
    expect(card.text()).toContain('A gate fails')
    expect(card.text()).toContain('Find the first divergence')
    await card.findAll('button').find((b) => b.text() === 'Load demo data')!.trigger('click')
    await flushPromises()
    expect(calls(fetchMock, 'POST', '/api/demo/seed')).toHaveLength(1)
    expect(calls(fetchMock, 'GET', '/api/overview')).toHaveLength(2)
    expect(page.text()).not.toContain('Nothing recorded yet')
    // The story now links to the failing evaluation and to the comparison with v2.
    const links = page.findAll('.start a').map((a) => a.attributes('href'))
    expect(links).toContain('/evaluations/suite-v1')
    expect(links).toContain('/compare?a=suite-v1&b=suite-v2')
  })

  it('remembers a dismissed walkthrough and can bring it back', async () => {
    const { page } = await open(overview())
    await page.findAll('.start button').find((b) => b.text() === 'Dismiss')!.trigger('click')
    expect(page.find('.start').exists()).toBe(false)
    expect(window.localStorage.getItem('agentscope.overview.start-dismissed')).toBe('1')
    await page.find('.restore button').trigger('click')
    expect(page.find('.start').exists()).toBe(true)
  })

  it('charts pass rate per agent version with the gate threshold, and offers a table', async () => {
    const { page, router } = await open(overview())
    const svg = page.find('svg.plot')
    expect(svg.attributes('role')).toBe('img')
    expect(svg.attributes('aria-label')).toContain('research_assistant@v1 60%')
    expect(svg.findAll('polyline')).toHaveLength(1) // v2 has a single point
    expect(svg.findAll('.marker')).toHaveLength(3)
    expect(svg.find('line.threshold').exists()).toBe(true)
    // The gate belongs to the newest gated suite (suite-v1): its rule is drawn for that line only.
    expect(page.find('.legend .rule').text()).toBe('gate minimum 80% · research_assistant@v1')
    expect(page.find('.legend').text()).toContain('research_assistant@v2')

    await page.findAll('[aria-label="Show the pass rate as"] button')[1]!.trigger('click')
    await flushPromises()
    expect(router.currentRoute.value.query.view).toBe('table')
    const rows = page.findAll('.pass-rate tbody tr')
    expect(rows).toHaveLength(3)
    expect(rows[2]!.text()).toContain('90%')
    expect(rows[2]!.find('a').attributes('href')).toBe('/evaluations/suite-v2')
  })

  it('draws one line per agent version and dataset, so another dataset never reads as a regression', async () => {
    const data = overview({
      pass_rate_series: [
        {
          agent_ref: 'research_assistant@v2',
          points: [
            { suite_id: 's1', finished_at: '2026-10-15T10:00:00Z', pass_rate: 21 / 29, dataset: 'research-assistant@1', gate_passed: null },
            { suite_id: 's2', finished_at: '2026-10-16T10:00:00Z', pass_rate: 0.2, dataset: 'imported@1', gate_passed: null },
            { suite_id: 's3', finished_at: '2026-10-16T11:00:00Z', pass_rate: 22 / 29, dataset: 'research-assistant@1', gate_passed: null },
          ],
        },
      ],
      recent_suites: [],
      latest_gate: null,
    })
    const { page } = await open(data)
    const svg = page.find('svg.plot')
    // research-assistant@1 is one line (s1 → s3); imported@1 is a single point, not a drop.
    expect(svg.findAll('polyline')).toHaveLength(1)
    expect(svg.findAll('.marker')).toHaveLength(3)
    expect(page.findAll('.legend li').map((li) => li.text())).toEqual([
      'research_assistant@v2 · imported@1',
      'research_assistant@v2 · research-assistant@1',
    ])
  })

  it('draws the gate minimum only for the line it was applied to', async () => {
    // The newest gated suite is a planner suite: its 90% minimum says nothing about the research agent.
    const planner = suiteItem({ suite_id: 'suite-tp', agent_ref: 'task_planner@v2', dataset_name: 'task-planner', gate_passed: true })
    const base = overview()
    const data = overview({
      recent_suites: [planner, ...base.recent_suites],
      pass_rate_series: [
        ...base.pass_rate_series,
        {
          agent_ref: 'task_planner@v2',
          points: [{ suite_id: 'suite-tp', finished_at: '2026-10-16T12:00:00Z', pass_rate: 0.95, dataset: 'task-planner@1', gate_passed: true }],
        },
      ],
      latest_gate: { ...base.latest_gate!, rules: [{ name: 'min_pass_rate', threshold: 0.9, actual: '19/20', passed: true, detail: '' }] },
    })
    let { page } = await open(data)
    expect(page.find('.legend .rule').text()).toBe('gate minimum 90% · task_planner@v2 · task-planner@1')
    expect(page.find('svg.plot').attributes('aria-label')).toContain('gate minimum 90% for task_planner@v2 · task-planner@1')
    page.unmount()
    ;({ page } = await open(data, '?agent=research_assistant@v1'))
    expect(page.find('svg.plot line.threshold').exists()).toBe(false)
    expect(page.find('.legend .rule').exists()).toBe(false)
  })

  it('says which line the gate belongs to when that line is past the charted ones', async () => {
    const refs = ['research_assistant@v1', 'research_assistant@v2', 'research_assistant@v3', 'task_planner@v1', 'task_planner@v2']
    const data = overview({
      recent_suites: [suiteItem({ suite_id: 'tp-v2', agent_ref: 'task_planner@v2', dataset_name: 'task-planner', gate_passed: true })],
      pass_rate_series: refs.map((ref, i) => ({
        agent_ref: ref,
        points: [
          {
            suite_id: ref === 'task_planner@v2' ? 'tp-v2' : `s${i}`,
            finished_at: `2026-10-16T1${i}:00:00Z`,
            pass_rate: 0.5,
            dataset: ref.startsWith('task') ? 'task-planner@1' : 'research-assistant@1',
            gate_passed: ref === 'task_planner@v2' ? true : null,
          },
        ],
      })),
      latest_gate: { passed: true, baseline_used: false, rules: [{ name: 'min_pass_rate', threshold: 0.6, actual: '19/27', passed: true, detail: '' }] },
    })
    const { page } = await open(data)
    expect(page.find('svg.plot line.threshold').exists()).toBe(false)
    expect(page.find('.pass-rate .more').text()).toContain(
      'The gate minimum of 60% applies to task_planner@v2 · task-planner@1, which is not charted',
    )
  })

  it('shows the first-run state on a fresh data dir, where only the built-in datasets exist', async () => {
    const { page } = await open({ ...EMPTY, counts: { runs: 0, suites: 0, datasets: 2, experiments: 0 } })
    expect(page.text()).toContain('Nothing recorded yet')
    expect(page.find('form.filters').exists()).toBe(false)
  })

  it('shows the latest gate, providers, recent evaluations and runs', async () => {
    const { page } = await open(overview())
    const gate = page.find('.gate')
    expect(gate.text()).toContain('gate failed')
    expect(gate.findAll('tbody tr')).toHaveLength(3)
    const providers = page.find('.providers')
    expect(providers.text()).toContain('deterministic')
    expect(providers.text()).toContain('Unreachable at 127.0.0.1:11434')
    expect(page.text()).toContain('6/10')
    expect(page.text()).toContain('12 runs · 2 evaluations · 1 dataset · 1 comparison')
    expect(page.findAll('a').some((a) => a.attributes('href') === '/runs/run-2')).toBe(true)
  })

  it('keeps line colours and shapes when filtering to one agent version', async () => {
    const { page } = await open(overview(), '?agent=research_assistant@v2')
    const markers = page.findAll('svg.plot .marker')
    expect(markers).toHaveLength(1)
    expect(markers[0]!.classes()).toContain('s2')
    expect(markers[0]!.attributes('data-shape')).toBe('square')
    expect(page.findAll('tbody tr').every((tr) => !tr.text().includes('research_assistant@v1'))).toBe(true)
  })

  it('says when a filter matches nothing', async () => {
    const { page, router } = await open(overview(), '?agent=task_planner@v9')
    expect(page.text()).toContain('Nothing recorded for this agent version')
    await page.findAll('button').find((b) => b.text() === 'Clear filter')!.trigger('click')
    await flushPromises()
    expect(router.currentRoute.value.query.agent).toBeUndefined()
  })

  it('tells an unreachable API apart from a pending migration', async () => {
    let { page } = await open(new TypeError('Failed to fetch'))
    expect(page.find('[role="alert"]').text()).toContain('Cannot reach the AgentScope API')
    expect(page.text()).toContain('agentscope serve')
    page.unmount()
    ;({ page } = await open(() => json({ detail: 'database revision 0001 is behind: run agentscope db upgrade' }, 503)))
    expect(page.find('[role="alert"]').text()).toContain('Database migration pending')
    expect(page.text()).toContain('agentscope db upgrade')
  })

  it('opens the New run drawer', async () => {
    const { page } = await open(overview())
    await page.findAll('button').find((b) => b.text() === 'New run')!.trigger('click')
    expect(newRun.open).toBe(true)
    newRun.open = false
  })
})
