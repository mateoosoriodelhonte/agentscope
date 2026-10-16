import { flushPromises, type VueWrapper } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { Router } from 'vue-router'
import { api } from '@/api/client'
import type { AgentsResponse, RunList, RunSummary } from '@/api/types'
import { mountAt } from '@/__tests__/router'
import RunsView from '../RunsView.vue'

let wrapper: VueWrapper | null = null

function summary(overrides: Partial<RunSummary> = {}): RunSummary {
  return {
    run_id: '6e2fdf5d-ec2f-4201-bfc2-cabeb227ff3e',
    trace_id: 'a'.repeat(32),
    agent: 'research_assistant',
    agent_version: 'v1',
    config_hash: 'sha256:abc',
    provider: 'agentscope.fake',
    model: 'scripted-naive',
    status: 'completed',
    error_code: null,
    task: 'In what year did the Meridian Footbridge open?',
    case_id: null,
    suite_id: null,
    started_at: '2026-10-16T21:39:10.998688Z',
    duration_ms: 12.5,
    steps: 4,
    tool_calls: 3,
    blocked_tool_calls: 1,
    guardrail_events: 2,
    faults: 0,
    passed: true,
    ...overrides,
  }
}

const AGENTS = {
  agents: [{ versions: [{ agent: 'research_assistant', version: 'v1' }, { agent: 'research_assistant', version: 'v2' }] }],
  tools: [],
} as unknown as AgentsResponse

async function open(list: RunList | Error, query = ''): Promise<{ page: VueWrapper; router: Router }> {
  const listRuns = vi.spyOn(api, 'listRuns')
  if (list instanceof Error) listRuns.mockRejectedValue(list)
  else listRuns.mockResolvedValue(list)
  const mounted = await mountAt([{ path: '/runs', component: RunsView }], `/runs${query}`)
  wrapper = mounted.page
  return mounted
}

beforeEach(() => {
  vi.spyOn(api, 'agents').mockResolvedValue(AGENTS)
})

afterEach(() => {
  wrapper?.unmount()
  wrapper = null
})

describe('RunsView', () => {
  it('lists runs with status, simulated-model tag, blocked calls and verdict', async () => {
    const { page } = await open({ items: [summary()], total: 1 })
    const row = page.find('tbody tr')
    expect(row.find('.badge').text()).toBe('completed')
    expect(row.find('a').attributes('href')).toBe('/runs/6e2fdf5d-ec2f-4201-bfc2-cabeb227ff3e')
    expect(row.text()).toContain('research_assistant@v1')
    expect(row.text()).toContain('simulated')
    expect(row.text()).toContain('1 blocked')
    expect(row.text()).toContain('passed')
    expect(page.find('.pager').text()).toContain('1–1 of 1')
  })

  it('holds filters in the URL and sends them to the API', async () => {
    const { page, router } = await open({ items: [summary()], total: 1 })
    const selects = page.findAll('select')
    await selects[0]!.setValue('failed')
    await flushPromises()
    expect(router.currentRoute.value.query).toEqual({ status: 'failed' })
    await selects[1]!.setValue('research_assistant@v2')
    await selects[2]!.setValue('yes')
    await selects[3]!.setValue('no')
    await selects[4]!.setValue('none')
    await flushPromises()
    expect(router.currentRoute.value.query).toEqual({
      status: 'failed',
      agent: 'research_assistant@v2',
      faults: 'true',
      blocked: 'false',
      verdict: 'none',
    })
    const calls = vi.mocked(api.listRuns).mock.calls
    expect(calls[calls.length - 1]![0]).toEqual({
      status: 'failed',
      agent: 'research_assistant',
      agent_version: 'v2',
      has_faults: true,
      has_blocked_calls: false,
      verdict: 'none',
      limit: 50,
      offset: 0,
    })
  })

  it('restores filters and the page from the URL', async () => {
    const { page } = await open({ items: [summary({ status: 'failed', passed: false })], total: 120 }, '?status=failed&page=3')
    expect((page.findAll('select')[0]!.element as HTMLSelectElement).value).toBe('failed')
    expect(vi.mocked(api.listRuns).mock.calls[0]![0]).toMatchObject({ status: 'failed', offset: 100 })
    expect(page.find('.pager').text()).toContain('101–101 of 120')
  })

  it('never shows rows that contradict the filters, and says so', async () => {
    const { page } = await open(
      { items: [summary({ passed: false }), summary({ run_id: 'other', passed: true })], total: 2 },
      '?verdict=failed',
    )
    expect(page.findAll('tbody tr')).toHaveLength(1)
    expect(page.find('.notice').text()).toContain('1 non-matching run on this page is hidden')
  })

  it('keeps paging when every row on a page was hidden by a filter the server ignored', async () => {
    // 120 runs exist; the server ignored ?verdict, so this page holds only passed runs.
    const items = Array.from({ length: 50 }, (_, i) => summary({ run_id: `run-${i}`, passed: true }))
    const { page, router } = await open({ items, total: 120 }, '?verdict=failed')
    expect(page.text()).not.toContain('No runs match these filters')
    expect(page.find('.empty-page').text()).toContain('No run on this page matches every filter')
    expect(page.find('.count').text()).toBe('120 runs before client-side filtering')
    expect(page.find('.pager').text()).toContain('1–50 of 120')
    const next = page.findAll('.pager button').find((b) => b.text() === 'Next')!
    expect(next.attributes('disabled')).toBeUndefined()
    await next.trigger('click')
    await flushPromises()
    expect(router.currentRoute.value.query).toMatchObject({ verdict: 'failed', page: '2' })
  })

  it('does not call a page past the end "no runs yet"', async () => {
    const { page } = await open({ items: [], total: 3 }, '?page=4')
    expect(page.text()).not.toContain('No runs yet')
    expect(page.find('.empty-page').text()).toContain('No runs on this page')
    expect(page.find('.pager').text()).toContain('page 4 of 1')
  })

  it('distinguishes a first run, an empty filter and an error', async () => {
    let { page } = await open({ items: [], total: 0 })
    expect(page.text()).toContain('No runs yet')
    expect(page.text()).toContain('agentscope run --agent research_assistant@v2')
    page.unmount()
    ;({ page } = await open({ items: [], total: 0 }, '?status=timeout'))
    expect(page.text()).toContain('No runs match these filters')
    page.unmount()
    ;({ page } = await open(new Error('Failed to fetch')))
    expect(page.find('[role="alert"]').text()).toContain('Failed to fetch')
    expect(page.text()).toContain('agentscope serve')
  })
})
