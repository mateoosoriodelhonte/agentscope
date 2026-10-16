import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import type { RunDetail } from '@/api/types'
import { loadFixture } from '@/__tests__/fixtures'
import ToolSequenceDiff, { diffToolSequence, expectedTools, executedTools } from '../ToolSequenceDiff.vue'
import VerdictPanel from '../VerdictPanel.vue'

function mountPanel(detail: RunDetail, activeCheck: string | null = null, evidenceSpans: string[] = []) {
  return mount(VerdictPanel, {
    props: {
      evaluation: detail.evaluation,
      evalCase: detail.case,
      trace: detail.trace,
      activeCheck,
      evidenceSpans,
      selectedSpan: evidenceSpans[0] ?? null,
    },
  })
}

describe('VerdictPanel', () => {
  const strict = loadFixture('run-v1-strict')

  it('lists failures first, each with icon + label, name and expected vs actual', () => {
    const panel = mountPanel(strict)
    const rows = panel.findAll('.checks > li')
    expect(rows.map((r) => r.find('.name').text())).toEqual([
      'required_tool_calls',
      'task_completion',
      'answer_correctness',
      'schema_validity',
    ])
    const first = rows[0]!
    expect(first.find('.badge').text()).toBe('failed')
    expect(first.find('.badge svg').exists()).toBe(true)
    expect(first.find('.line').text()).toBe(
      'expected search_documents ≥1×, retrieve_document ≥2× · executed search_documents 1×, retrieve_document 1×',
    )
    expect(rows[1]!.find('.line').text()).toBe(
      'expected completed, ≤2 tool calls · got completed, 4 steps, 3 tool calls',
    )
    expect(rows[2]!.find('.line').text()).toBe('expected "1987" · got "1987"')
    expect(panel.find('.head').text()).toContain('2 failed · 2 passed')
  })

  it('activates and deactivates a check', async () => {
    const panel = mountPanel(strict, 'task_completion')
    await panel.findAll('.check')[1]!.trigger('click')
    await panel.findAll('.check')[0]!.trigger('click')
    expect(panel.emitted('activate')).toEqual([[null], ['required_tool_calls']])
    expect(panel.findAll('.check')[1]!.attributes('aria-expanded')).toBe('true')
  })

  it('links span evidence back to the trace', async () => {
    const answer = strict.evaluation!.checks.find((c) => c.name === 'answer_correctness')!
    const spanId = answer.evidence[0]!.span_id!
    const panel = mountPanel(strict, 'answer_correctness', [spanId])
    expect(panel.find('.cycle').text()).toContain('Evidence span 1 of 1')
    const jump = panel.find('.jump')
    expect(jump.attributes('aria-current')).toBe('true')
    await jump.trigger('click')
    expect(panel.emitted('jump')).toEqual([[spanId]])
    expect(panel.findComponent(ToolSequenceDiff).exists()).toBe(false)
  })

  it('explains span-less violations instead of linking them', () => {
    const panel = mountPanel(strict, 'task_completion')
    const evidence = panel.find('.evidence li')
    expect(evidence.text()).toContain('3 tool calls > budget 2')
    expect(evidence.text()).toContain('no span: judged on the run as a whole')
    expect(evidence.find('.jump').exists()).toBe(false)
  })

  it('shows an expected vs executed tool sequence for absent evidence', () => {
    const required = strict.evaluation!.checks.find((c) => c.name === 'required_tool_calls')!
    const supportSpan = required.evidence.find((e) => e.role === 'support')!.span_id!
    const panel = mountPanel(strict, 'required_tool_calls', [supportSpan])
    const diff = panel.findComponent(ToolSequenceDiff)
    expect(diff.exists()).toBe(true)
    const [expected, executed] = diff.findAll('[role="row"]')
    expect(expected!.findAll('.chip').map((c) => c.text())).toEqual([
      'search_documents',
      'retrieve_document',
      'retrieve_document',
    ])
    const ghosts = executed!.findAll('.chip.ghost')
    expect(ghosts).toHaveLength(1)
    expect(ghosts[0]!.text()).toBe('missing:retrieve_document')
    expect(diff.text()).toContain('1 blocked call is not shown')
  })

  it('offers an unevaluated run as a test case snippet', () => {
    const detail = { ...loadFixture('run-v2'), evaluation: null, case: null }
    const panel = mountPanel(detail)
    const snippet = JSON.parse(panel.find('.snippet pre').text()) as Record<string, unknown>
    expect(snippet).toEqual({
      id: 'my-case',
      task: 'In what year did the Meridian Footbridge open?',
      expected: { field: 'answer', match: 'normalized', value: '1987' },
    })
  })
})

describe('diffToolSequence', () => {
  it('aligns sequences and marks missing and extra calls', () => {
    expect(diffToolSequence(['a', 'b', 'b'], ['a', 'b'])).toEqual([
      { kind: 'match', tool: 'a' },
      { kind: 'match', tool: 'b' },
      { kind: 'missing', tool: 'b' },
    ])
    expect(diffToolSequence(['a', 'c'], ['a', 'x', 'c'])).toEqual([
      { kind: 'match', tool: 'a' },
      { kind: 'extra', tool: 'x' },
      { kind: 'match', tool: 'c' },
    ])
  })

  it('derives the sequences from the case and the executed calls', () => {
    const strict = loadFixture('run-v1-strict')
    expect(expectedTools(strict.case!)).toEqual(['search_documents', 'retrieve_document', 'retrieve_document'])
    expect(executedTools(strict.trace)).toEqual(['search_documents', 'retrieve_document'])
    expect(expectedTools({ ...strict.case!, tool_order: ['retrieve_document'] })).toEqual(['retrieve_document'])
  })
})
