import { flushPromises, type VueWrapper } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { Router } from 'vue-router'
import { api, ApiError } from '@/api/client'
import type { RunDetail } from '@/api/types'
import { loadFixture } from '@/__tests__/fixtures'
import { mountAt } from '@/__tests__/router'
import TraceView from '../TraceView.vue'

let wrapper: VueWrapper | null = null

const ROUTES = [{ path: '/runs/:runId', component: TraceView, props: true }]

async function open(detail: RunDetail, query = ''): Promise<{ page: VueWrapper; router: Router }> {
  vi.spyOn(api, 'getRun').mockResolvedValue(detail)
  const mounted = await mountAt(ROUTES, `/runs/${detail.trace.run_id}${query}`)
  wrapper = mounted.page
  return mounted
}

const lastChat = (detail: RunDetail) => detail.trace.spans.filter((s) => s.kind === 'llm').pop()!

const selected = (page: VueWrapper) => page.find('tr[aria-selected="true"]')
const press = (page: VueWrapper, key: string, target = '.rail') =>
  page.find(target).trigger('keydown', { key })

afterEach(() => {
  wrapper?.unmount()
  wrapper = null
})

describe('TraceView', () => {
  const strict = loadFixture('run-v1-strict')
  const answer = strict.evaluation!.checks.find((c) => c.name === 'answer_correctness')!
  const answerSpan = answer.evidence[0]!.span_id!

  it('shows the run header with honest metrics and the simulated-model badge', async () => {
    const { page } = await open(strict)
    const header = page.find('.run-header')
    expect(header.find('h1').text()).toBe('In what year did the Meridian Footbridge open?')
    expect(header.text()).toContain('research_assistant@v1')
    expect(header.text()).toContain('Simulated model: demonstrates harness mechanics, not model behaviour')
    expect(header.text()).toMatch(/Tokens\s*not reported/)
    expect(header.text()).toMatch(/Cost\s*not configured/)
    expect(header.text()).not.toContain('$0')
  })

  it('activating a check sets ?check=, selects, expands and outlines its evidence', async () => {
    const { page, router } = await open(strict)
    const check = page.findAll('.check').find((c) => c.text().includes('answer_correctness'))!
    await check.trigger('click')
    await flushPromises()
    expect(router.currentRoute.value.query).toMatchObject({ check: 'answer_correctness', span: answerSpan })
    const row = selected(page)
    expect(row.attributes('id')).toBe(`trace-row-${answerSpan}`)
    expect(row.classes()).toContain('evidence')
    expect(page.find('#trace-row-step-4').attributes('aria-expanded')).toBe('true')
    expect(page.find('.detail .head').text()).toContain('chat scripted-naive')
    expect(page.find('.citations').text()).toContain('answer_correctness')
  })

  it('opens the evidence of a deep-linked check', async () => {
    const { page, router } = await open(strict, '?check=answer_correctness')
    expect(router.currentRoute.value.query.span).toBe(answerSpan)
    expect(selected(page).attributes('id')).toBe(`trace-row-${answerSpan}`)
  })

  it('shows the tool sequence diff for a check failed by absence', async () => {
    const { page } = await open(strict, '?check=required_tool_calls')
    expect(page.find('.chip.ghost').text()).toContain('retrieve_document')
  })

  it('runs region shortcuts from the registry while the trace has focus', async () => {
    const { page, router } = await open(strict, '?check=answer_correctness')
    await press(page, 't')
    await flushPromises()
    expect(router.currentRoute.value.query.mode).toBe('timeline')
    await press(page, 'n')
    await flushPromises()
    expect(router.currentRoute.value.query.span).toBe(answerSpan) // a single evidence span
    await press(page, '2')
    await flushPromises()
    expect(router.currentRoute.value.query.tab).toBe('messages')
    await press(page, 'j')
    await flushPromises()
    expect(router.currentRoute.value.query.span).not.toBe(answerSpan)
    await press(page, 'Escape')
    await flushPromises()
    expect(router.currentRoute.value.query.check).toBeUndefined()
    await press(page, ']')
    expect(page.findAll('tr[aria-expanded="false"]')).toHaveLength(0)
    await press(page, '[')
    expect(page.findAll('tr[aria-expanded="true"]')).toHaveLength(0)
  })

  it('keeps arrow keys for other widgets outside the tree', async () => {
    const { page, router } = await open(strict)
    await press(page, 'ArrowDown')
    await flushPromises()
    expect(router.currentRoute.value.query.span).toBeUndefined()
    await press(page, 'ArrowDown', '[role="treegrid"]')
    await flushPromises()
    expect(router.currentRoute.value.query.span).toBe('step-1')
  })

  it('ignores shortcuts typed with modifiers', async () => {
    const { page, router } = await open(strict)
    await page.find('.rail').trigger('keydown', { key: 't', metaKey: true })
    await flushPromises()
    expect(router.currentRoute.value.query.mode).toBeUndefined()
  })

  it('opens a shortcut overlay generated from the registry', async () => {
    const { page } = await open(strict)
    await press(page, '?')
    const dialog = page.find('[role="dialog"]')
    expect(dialog.exists()).toBe(true)
    expect(dialog.text()).toContain('Duration bars: relative / timeline')
    expect(dialog.text()).toContain('Next evidence span of the active check')
    await dialog.trigger('keydown', { key: 'Escape' })
    expect(page.find('[role="dialog"]').exists()).toBe(false)
  })

  it('shows tool tabs, attempts and the injected fault for a faulted call', async () => {
    const timeout = loadFixture('run-v2-timeout')
    const attempt = timeout.trace.tool_invocations[0]!.attempts[0]!.span_id!
    const { page } = await open(timeout, `?span=${attempt}&tab=attempts`)
    expect(page.find('[role="tablist"]').text()).toBe('CallResultAttemptsRaw')
    expect(page.find('.banner').text()).toContain('Injected fault: timeout')
    const rows = page.findAll('.attempts tbody tr')
    expect(rows.map((r) => r.find('.badge').text())).toEqual(['timeout', 'ok'])
    expect(rows[0]!.text()).toContain('injected timeout')
  })

  it('shows plain-text model input with untrusted frames only when isolation is on', async () => {
    const isolated = loadFixture('run-v2')
    let { page } = await open(isolated, `?span=${lastChat(isolated).span_id}&tab=messages`)
    expect(page.findAll('.messages .open').length).toBeGreaterThan(0)
    page.unmount()
    const raw = loadFixture('run-v1')
    ;({ page } = await open(raw, `?span=${lastChat(raw).span_id}&tab=messages`))
    expect(page.findAll('.messages .open')).toHaveLength(0)
    expect(page.find('.messages').text()).toContain('Result of search_documents')
  })

  it('explains a missing run', async () => {
    vi.spyOn(api, 'getRun').mockRejectedValue(new ApiError(404, 'run not found'))
    const { page } = await mountAt(ROUTES, '/runs/nope')
    wrapper = page
    expect(page.find('[role="alert"]').text()).toContain('Run not found')
    expect(page.find('h1').text()).toBe('Run nope')
  })
})
