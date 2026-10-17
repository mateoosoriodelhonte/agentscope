import { describe, expect, it } from 'vitest'
import { loadFixture } from '@/__tests__/fixtures'
import { alignSteps, differingFacets, divergences, stepSignature, traceSteps, type Alignable } from '../alignSteps'

/** Steps written as "signature" or "signature=content". */
function steps(...specs: string[]): Alignable[] {
  return specs.map((spec) => {
    const [signature, content = ''] = spec.split('=')
    return { signature: signature!, content }
  })
}

const shape = (rows: ReturnType<typeof alignSteps>) =>
  rows.map((row) => `${row.kind}:${row.a?.signature ?? '-'}/${row.b?.signature ?? '-'}`)

describe('alignSteps', () => {
  it('matches identical runs step for step', () => {
    const rows = alignSteps(steps('tool_call:search', 'final'), steps('tool_call:search', 'final'))
    expect(shape(rows)).toEqual(['same:tool_call:search/tool_call:search', 'same:final/final'])
    expect(divergences(rows)).toEqual([])
  })

  it('turns an extra step in A into a gap row on the B side', () => {
    const rows = alignSteps(
      steps('tool_call:search', 'tool_call:send_email', 'tool_call:retrieve', 'final'),
      steps('tool_call:search', 'tool_call:retrieve', 'final'),
    )
    expect(shape(rows)).toEqual([
      'same:tool_call:search/tool_call:search',
      'only_a:tool_call:send_email/-',
      'same:tool_call:retrieve/tool_call:retrieve',
      'same:final/final',
    ])
    expect(divergences(rows)).toEqual([1])
  })

  it('turns an extra step in B into a gap row on the A side', () => {
    const rows = alignSteps(steps('tool_call:search', 'final'), steps('tool_call:search', 'tool_call:retrieve', 'final'))
    expect(shape(rows)).toEqual([
      'same:tool_call:search/tool_call:search',
      'only_b:-/tool_call:retrieve',
      'same:final/final',
    ])
  })

  it('marks a matched step whose content differs as changed', () => {
    const rows = alignSteps(steps('tool_call:search=q1', 'final=1987'), steps('tool_call:search=q1', 'final=1986'))
    expect(rows.map((r) => r.kind)).toEqual(['same', 'changed'])
    expect(divergences(rows)).toEqual([1])
  })

  it('lists A-only steps before B-only steps between two matches', () => {
    const rows = alignSteps(steps('tool_call:a', 'tool_call:x', 'final'), steps('tool_call:a', 'tool_call:y', 'final'))
    expect(shape(rows)).toEqual([
      'same:tool_call:a/tool_call:a',
      'only_a:tool_call:x/-',
      'only_b:-/tool_call:y',
      'same:final/final',
    ])
  })

  it('keeps the longest common subsequence when steps are reordered', () => {
    const rows = alignSteps(steps('tool_call:a', 'tool_call:b', 'tool_call:c'), steps('tool_call:b', 'tool_call:c', 'tool_call:a'))
    expect(rows.filter((r) => r.kind === 'same').map((r) => r.a!.signature)).toEqual(['tool_call:b', 'tool_call:c'])
    expect(rows).toHaveLength(4)
  })

  it('handles empty runs', () => {
    expect(alignSteps([], [])).toEqual([])
    expect(shape(alignSteps(steps('final'), []))).toEqual(['only_a:final/-'])
  })
})

describe('traceSteps', () => {
  it('signs each step with its decision type and tool', () => {
    expect(stepSignature('tool_call', 'search_documents')).toBe('tool_call:search_documents')
    expect(stepSignature('final', null)).toBe('final')
    expect(traceSteps(loadFixture('run-v1').trace).map((s) => s.signature)).toEqual([
      'tool_call:search_documents',
      'tool_call:send_email',
      'tool_call:retrieve_document',
      'final',
    ])
  })

  it('finds where v1 and v2 part: different search results, then the injected send_email', () => {
    const rows = alignSteps(traceSteps(loadFixture('run-v1').trace), traceSteps(loadFixture('run-v2').trace))
    expect(rows.map((r) => r.kind)).toEqual(['changed', 'only_a', 'same', 'changed'])
    const [first, second] = divergences(rows).map((i) => rows[i]!)
    // Same call and arguments; v2's verified-only search schema returned other documents.
    expect(differingFacets(first!.a!, first!.b!)).toEqual(['the tool result'])
    expect(second!.a).toMatchObject({ index: 2, tool: 'send_email', rowId: 'step-2' })
    expect(second!.a!.narrative).toContain('Blocked send_email')
  })

  it('ignores ids, timestamps and durations: two recordings of one run are identical', () => {
    const a = loadFixture('run-v2').trace
    const b = loadFixture('run-v2').trace
    b.run_id = 'another-run'
    for (const span of b.spans) {
      span.span_id = `x${span.span_id}`
      span.parent_span_id = span.parent_span_id ? `x${span.parent_span_id}` : null
      span.duration_ms *= 3
    }
    for (const inv of b.tool_invocations) inv.call_id = `other-${inv.call_id}`
    expect(divergences(alignSteps(traceSteps(a), traceSteps(b)))).toEqual([])
  })

  it('sees a timeout fault as a change in the same step', () => {
    const rows = alignSteps(traceSteps(loadFixture('run-v2').trace), traceSteps(loadFixture('run-v2-timeout').trace))
    expect(rows.map((r) => r.kind)).toEqual(['changed', 'same', 'same'])
  })
})
