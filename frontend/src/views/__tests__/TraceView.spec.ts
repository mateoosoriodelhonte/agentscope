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
    // No tokens were reported, so no cost could be computed whatever the rates.
    expect(header.text()).toMatch(/Cost\s*not reported/)
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

  it('keeps focus on the page heading when the loading heading is replaced', async () => {
    let resolve: (value: RunDetail) => void = () => undefined
    vi.spyOn(api, 'getRun').mockReturnValue(new Promise((r) => (resolve = r)))
    const { page } = await mountAt(ROUTES, `/runs/${strict.trace.run_id}`)
    wrapper = page
    ;(page.find('h1').element as HTMLElement).focus()
    expect(document.activeElement?.textContent).toContain('Run ')
    resolve(strict)
    await flushPromises()
    expect(document.activeElement?.tagName).toBe('H1')
    expect(document.activeElement?.textContent).toBe(strict.trace.task)
  })

  it('describes the scope of each injected fault exactly', async () => {
    const detail = structuredClone(loadFixture('run-v2-timeout'))
    detail.trace.faults = [
      { tool: 'search_documents', kind: 'timeout', on_call: 1, times: 1, payload: null },
      { tool: 'search_documents', kind: 'error', on_call: 2, times: null, payload: null },
      { tool: 'retrieve_document', kind: 'malformed', on_call: null, times: 1, payload: null },
      { tool: 'retrieve_document', kind: 'error', on_call: 2, times: 3, payload: null },
    ]
    const { page } = await open(detail)
    expect(page.findAll('.faults .fault').map((f) => f.text())).toEqual([
      'search_documents: timeout on call 1',
      'search_documents: error from call 2 onward',
      'retrieve_document: malformed on every call',
      'retrieve_document: error on calls 2–4',
    ])
  })

  it('says the cost is not reported when the provider reported no tokens, even with rates configured', async () => {
    const detail = structuredClone(strict)
    detail.trace.usage = { input_tokens: null, output_tokens: null, reported_calls: 0, total_calls: 3, source: 'provider_reported' }
    const { page } = await open(detail)
    expect(page.find('.run-header').text()).toMatch(/Cost\s*not reported/)
    expect(page.find('.run-header').text()).not.toContain('not configured')
  })

  it('says the cost is not configured only when tokens were reported but no rate exists', async () => {
    const detail = structuredClone(strict)
    detail.trace.usage = { input_tokens: 120, output_tokens: 30, reported_calls: 3, total_calls: 3, source: 'provider_reported' }
    const { page } = await open(detail)
    expect(page.find('.run-header').text()).toMatch(/Tokens\s*120 in · 30 out/)
    expect(page.find('.run-header').text()).toMatch(/Cost\s*not configured/)
  })

  it('keeps keyboard focus in the tree when collapsing all from a nested row', async () => {
    const { page } = await open(strict, '?check=answer_correctness')
    await flushPromises()
    const row = page.find(`#trace-row-${answerSpan}`)
    expect(row.attributes('aria-level')).toBe('3')
    ;(row.element as HTMLElement).focus()
    await row.trigger('keydown', { key: '[' })
    await flushPromises()
    expect(page.find(`#trace-row-${answerSpan}`).exists()).toBe(false)
    // Focus moves to the nearest visible ancestor, so the trace shortcuts keep working.
    expect(document.activeElement?.id).toBe('trace-row-step-4')
    await page.find('#trace-row-step-4').trigger('keydown', { key: ']' })
    await flushPromises()
    expect(page.find('#trace-row-step-4').attributes('aria-expanded')).toBe('true')
  })

  it('labels a configured cost as an estimate', async () => {
    const detail = structuredClone(strict)
    detail.trace.cost = {
      amount: 0.01234,
      currency: 'USD',
      input_per_1k_tokens: 0.1,
      output_per_1k_tokens: 0.2,
      partial: false,
      source: 'estimate_from_configured_rates',
    }
    const { page } = await open(detail)
    expect(page.find('.run-header').text()).toMatch(/Cost\s*\$0\.0123 est\./)
  })

  it('finds the observation by its exact header, not by a call id mentioned in tool output', async () => {
    const detail = structuredClone(loadFixture('run-v1'))
    // Hostile tool output on call_001 that mentions call_003.
    for (const span of detail.trace.spans) {
      const messages = (span.input as { messages?: { role: string; content: string }[] } | null)?.messages ?? []
      for (const m of messages) {
        if (m.role === 'tool' && m.content.startsWith('Result of search_documents (call call_001)')) {
          m.content += ' see call_003'
        }
      }
    }
    const step3Observe = detail.trace.spans.filter((s) => s.name === 'node observe')[2]!
    const { page } = await open(detail, `?span=${step3Observe.span_id}&tab=observation`)
    expect(page.find('.messages').text()).toContain('Result of retrieve_document (call call_003)')
    expect(page.find('.messages').text()).not.toContain('search_documents')
  })

  it('explains a missing run', async () => {
    vi.spyOn(api, 'getRun').mockRejectedValue(new ApiError(404, 'run not found'))
    const { page } = await mountAt(ROUTES, '/runs/nope')
    wrapper = page
    expect(page.find('[role="alert"]').text()).toContain('Run not found')
    expect(page.find('h1').text()).toBe('Run nope')
  })
})
