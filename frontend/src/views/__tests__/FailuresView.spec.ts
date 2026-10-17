import { flushPromises, type VueWrapper } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { failureAnalysis, suiteItem } from '@/__tests__/builders'
import { calls, mockFetch, never, type Reply } from '@/__tests__/mockFetch'
import { elsewhere, mountAt } from '@/__tests__/router'
import FailuresView from '../FailuresView.vue'

let wrapper: VueWrapper | null = null

async function open(analysis: Reply, query = '') {
  const fetchMock = mockFetch({
    'GET /api/failures': analysis,
    'GET /api/evaluations': [suiteItem(), suiteItem({ suite_id: 'suite-v2', agent_ref: 'research_assistant@v2' })],
  })
  const mounted = await mountAt([{ path: '/failures', component: FailuresView }, elsewhere], `/failures${query}`)
  wrapper = mounted.page
  return { ...mounted, fetchMock }
}

afterEach(() => {
  wrapper?.unmount()
  wrapper = null
  vi.unstubAllGlobals()
})

describe('FailuresView', () => {
  it('tabulates recovered / degraded / failed per fault kind and agent version, with totals', async () => {
    const { page } = await open(failureAnalysis())
    const table = page.find('.resilience')
    expect(table.findAll('thead th.agent').map((th) => th.text())).toEqual(['research_assistant@v1', 'research_assistant@v2'])
    const rows = table.findAll('tbody tr')
    expect(rows.map((r) => r.find('th').text())).toEqual(['error', 'timeout'])
    expect(rows[0]!.text()).toContain('not tested')
    expect(rows[1]!.findAll('td').map((td) => td.text())).toEqual(['0', '0', '2', '0/2', '1', '1', '0', '2/2'])
    expect(rows[1]!.findAll('td')[2]!.classes()).toContain('bad')
    expect(table.find('tfoot').findAll('td').map((td) => td.text())).toEqual(['0', '0', '2', '0/2', '2', '1', '0', '3/3'])
  })

  it('lists failure buckets, largest first, with at most three example runs', async () => {
    const { page } = await open(failureAnalysis())
    const rows = page.findAll('[aria-labelledby="buckets-title"] tbody tr')
    expect(rows[0]!.text()).toContain('policy_compliance')
    expect(rows[0]!.text()).toContain('leaked_content')
    const examples = rows[1]!.findAll('.examples a')
    expect(examples).toHaveLength(3)
    expect(examples[0]!.attributes('href')).toBe('/runs/r1?check=error_recovery')
  })

  it('labels injection attempts as simulated only for the simulated model, and executions as enforced', async () => {
    const { page } = await open(failureAnalysis())
    const cards = page.findAll('.injection .card')
    expect(cards[0]!.text()).toContain('Unauthorized attempts (simulated susceptibility)')
    expect(cards[1]!.text()).not.toContain('simulated susceptibility')
    for (const card of cards) {
      expect(card.text()).toContain('Unauthorized executions (enforced by runtime permissions, must be 0)')
      expect(card.text()).toContain('contained')
    }
    expect(cards[0]!.text()).toContain('Cases that leaked protected content2')
  })

  it('sends the filters, filters by agent here too, and says when the server ignored them', async () => {
    const { page, fetchMock } = await open(failureAnalysis(), '?agent=research_assistant@v2&suite=suite-v2')
    expect(calls(fetchMock, 'GET', '/api/failures')[0]!.url.search).toBe('?agent_ref=research_assistant%40v2&suite_id=suite-v2')
    expect(page.findAll('.resilience thead th.agent').map((th) => th.text())).toEqual(['research_assistant@v2'])
    expect(page.findAll('.injection .card')).toHaveLength(1)
    expect(page.text()).toContain('The server did not apply the evaluation filter')
    expect(page.text()).toContain('the failure buckets still cover every agent version')
  })

  it('reloads when a filter changes', async () => {
    const { page, router, fetchMock } = await open(failureAnalysis())
    await page.findAll('select')[0]!.setValue('research_assistant@v1')
    await flushPromises()
    expect(router.currentRoute.value.query.agent).toBe('research_assistant@v1')
    expect(calls(fetchMock, 'GET', '/api/failures')).toHaveLength(2)
  })

  it('has loading, first-run, filtered and error states', async () => {
    let { page } = await open(() => never())
    expect(page.find('.skeleton').exists()).toBe(true)
    page.unmount()
    const empty = failureAnalysis({ suites_considered: [], resilience: [], buckets: [], injection: [] })
    ;({ page } = await open(empty))
    expect(page.text()).toContain('No evaluations to analyse yet')
    page.unmount()
    ;({ page } = await open(failureAnalysis({ buckets: [] }), '?agent=task_planner@v1'))
    expect(page.text()).toContain('No failures recorded for these filters')
    page.unmount()
    ;({ page } = await open(new TypeError('Failed to fetch')))
    expect(page.find('[role="alert"]').text()).toContain('Cannot reach the AgentScope API')
  })
})
