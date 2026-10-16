import { describe, expect, it } from 'vitest'
import type { SpanRecord, TraceRecord } from '@/api/types'
import { loadFixture } from '@/__tests__/fixtures'
import { ancestorIds, decisionOf, epochMs, useTraceModel, visibleRows } from '../useTraceModel'

const narratives = (trace: TraceRecord) => useTraceModel(trace).groups.map((g) => g.narrative)

describe('useTraceModel', () => {
  it('groups a basic run into steps with narratives built from structured data', () => {
    const { trace } = loadFixture('run-v1')
    const model = useTraceModel(trace)
    expect(model.groups.map((g) => g.label)).toEqual(['Step 1', 'Step 2', 'Step 3', 'Step 4', 'Finish'])
    expect(narratives(trace)).toEqual([
      'Called search_documents → ok (3 results)',
      'Blocked send_email: not permitted',
      'Called retrieve_document → ok',
      'Final answer: 1987',
      'Completed in 4 steps',
    ])
    expect(model.groups.map((g) => g.outcome.label)).toEqual(['ok', 'blocked', 'ok', 'final', 'completed'])
    expect(model.simulated).toBe(true)
  })

  it('marks guard denials as off-loop transitions', () => {
    const model = useTraceModel(loadFixture('run-v1').trace)
    const step2 = model.groups[1]!
    expect(step2.outcome).toEqual({ tone: 'warn', label: 'blocked' })
    expect(step2.chips).toEqual([
      expect.objectContaining({ kind: 'denied', label: 'denied send_email', tone: 'warn' }),
    ])
    expect(model.groups.filter((g) => g.chips.length)).toHaveLength(1)
  })

  it('reports runtime retries and the injected fault on the failed attempt', () => {
    const { trace } = loadFixture('run-v2-timeout')
    const model = useTraceModel(trace)
    const step1 = model.groups[0]!
    expect(step1.narrative).toBe('Called search_documents → ok after 2 attempts (3 results)')
    expect(step1.chips).toEqual([expect.objectContaining({ kind: 'retry', label: 'retry ×1' })])
    expect(step1.hasFault).toBe(true)

    const attempts = model.rows.filter((r) => r.kind === 'tool' && r.groupId === 'step-1')
    expect(attempts.map((r) => r.label)).toEqual([
      'execute_tool search_documents · attempt 1',
      'execute_tool search_documents · attempt 2',
    ])
    expect(attempts.map((r) => r.fault)).toEqual(['timeout', null])
    expect(attempts.map((r) => r.outcome.label)).toEqual(['timeout', 'ok'])
    // The timed-out attempt dominates the relative scale, so the timeout tick is in range.
    expect(model.relativeScaleMs).toBeGreaterThanOrEqual(model.toolTimeoutMs)
  })

  it('narrates a validate → plan output repair after an injected model fault', () => {
    const { trace } = loadFixture('run-v2-schema-repair')
    const model = useTraceModel(trace)
    expect(narratives(trace)).toEqual([
      'Final answer rejected: schema invalid → repair 1/1 · injected schema_violation',
      'Called search_documents → ok (3 results)',
      'Called retrieve_document → ok',
      'Final answer: 1987',
      'Completed in 4 steps',
    ])
    expect(model.groups[0]!.chips).toEqual([expect.objectContaining({ kind: 'repair', label: 'repair 1/1' })])
    expect(model.groups[0]!.outcome).toEqual({ tone: 'fail', label: 'rejected' })
    const chat = model.rows.find((r) => r.kind === 'llm' && r.groupId === 'step-1')!
    expect(chat.fault).toBe('schema_violation')
    const validate = model.rows.find((r) => r.label === 'validate' && r.groupId === 'step-1')!
    expect(validate.outcome).toEqual({ tone: 'fail', label: 'invalid' })
  })

  it('keeps validated runs to one step per model decision', () => {
    expect(narratives(loadFixture('run-v2').trace)).toEqual([
      'Called search_documents → ok (3 results)',
      'Called retrieve_document → ok',
      'Final answer: 1987',
      'Completed in 3 steps',
    ])
    expect(useTraceModel(loadFixture('run-v3').trace).groups).toHaveLength(5)
  })

  it('builds a three-level tree in display order', () => {
    const model = useTraceModel(loadFixture('run-v1').trace)
    const [run, step1, plan, chat] = model.rows
    expect(run).toMatchObject({ kind: 'run', level: 1, parentId: null, label: 'Run' })
    expect(step1).toMatchObject({ id: 'step-1', kind: 'step', level: 1 })
    expect(plan).toMatchObject({ kind: 'node', level: 2, parentId: 'step-1', label: 'plan' })
    expect(chat).toMatchObject({ kind: 'llm', level: 3, parentId: plan!.id })
    expect(step1!.childIds.map((id) => model.rowById.get(id)!.label)).toEqual(['plan', 'guard', 'act', 'observe'])
    // Every span appears exactly once.
    expect(model.rows.filter((r) => r.span)).toHaveLength(model.rows.length - model.groups.length)
    expect(new Set(model.rows.map((r) => r.id)).size).toBe(model.rows.length)
  })

  it('places steps on the run time axis', () => {
    const model = useTraceModel(loadFixture('run-v2').trace)
    const steps = model.rows.filter((r) => r.kind === 'step')
    for (const [i, step] of steps.entries()) {
      expect(step.startMs).toBeGreaterThanOrEqual(0)
      expect(step.durationMs).toBeGreaterThan(0)
      if (i > 0) expect(step.startMs).toBeGreaterThan(steps[i - 1]!.startMs)
    }
    expect(model.totalMs).toBeGreaterThan(steps[steps.length - 1]!.startMs)
  })

  it('shows only expanded branches and resolves ancestors', () => {
    const model = useTraceModel(loadFixture('run-v1').trace)
    expect(visibleRows(model, new Set()).map((r) => r.label)).toEqual([
      'Run',
      'Step 1',
      'Step 2',
      'Step 3',
      'Step 4',
      'Finish',
    ])
    const leaf = model.rows.find((r) => r.kind === 'tool')!
    expect(ancestorIds(model, leaf.id)).toEqual([leaf.parentId, 'step-1'])
    const open = visibleRows(model, new Set(['step-1']))
    expect(open.map((r) => r.label).slice(1, 6)).toEqual(['Step 1', 'plan', 'guard', 'act', 'observe'])
  })

  it('puts a step-budget check without a model call in the finish group', () => {
    // Derived from a real trace: append the plan node the runtime records when the step
    // budget is exhausted (no chat span, routed to finish) and end the run with max_steps.
    const trace = structuredClone(loadFixture('run-v1').trace)
    const finish = trace.spans.find((s) => s.name === 'node finish')!
    const budget: SpanRecord = {
      ...finish,
      span_id: 'b'.repeat(16),
      name: 'node plan',
      attributes: { 'agentscope.node': 'plan', 'agentscope.step': 4, 'agentscope.route': 'finish' },
    }
    trace.spans.splice(trace.spans.indexOf(finish), 0, budget)
    trace.status = 'max_steps'
    trace.error_code = 'max_steps'
    trace.error = 'no final answer within 4 steps'
    const model = useTraceModel(trace)
    const end = model.groups[model.groups.length - 1]!
    expect(model.groups).toHaveLength(5)
    expect(end.narrative).toBe('Max steps: no final answer within 4 steps')
    expect(end.chips).toEqual([expect.objectContaining({ kind: 'terminal', label: 'max steps', tone: 'fail' })])
    expect(model.rowById.get('finish')!.childIds).toEqual([budget.span_id, finish.span_id])
  })

  it('parses decisions and keeps sub-millisecond timestamps', () => {
    const { trace } = loadFixture('run-v2')
    const chat = trace.spans.find((s) => s.kind === 'llm')!
    expect(decisionOf(chat)?.value).toMatchObject({ type: 'tool_call', tool: 'search_documents' })
    expect(decisionOf({ ...chat, output: { content: 'not json' } })).toEqual({ raw: 'not json', value: null })
    expect(epochMs('2026-10-16T21:39:11.001984Z') - epochMs('2026-10-16T21:39:11.001955Z')).toBeCloseTo(0.029, 3)
  })
})
