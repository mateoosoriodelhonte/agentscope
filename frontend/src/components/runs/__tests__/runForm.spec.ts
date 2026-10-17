import { describe, expect, it } from 'vitest'
import { V2 } from '@/__tests__/builders'
import type { FieldIssue } from '@/composables/parseJson'
import { buildExpected, buildFaults, expectedDraft, parseAgentConfig, validateAgentConfig } from '../runForm'

describe('validateAgentConfig', () => {
  it('accepts a real configuration', () => {
    expect(validateAgentConfig(V2)).toEqual([])
  })

  it('points at missing, unknown and out-of-range fields by JSON pointer', () => {
    const withoutTools: Partial<typeof V2> = { ...V2 }
    delete withoutTools.tools
    const issues = validateAgentConfig({
      ...withoutTools,
      agent: 'Research Assistant',
      model: { ...V2.model, temperature: 3 },
      limits: { ...V2.limits, max_steps: 0, speed: 'fast' },
      retry: { ...V2.retry, retry_on: ['timeout', 'sometimes'] },
    })
    expect(issues).toEqual(
      expect.arrayContaining([
        { path: '/tools', message: 'is required' },
        { path: '/agent', message: expect.stringContaining('does not match') },
        { path: '/model/temperature', message: 'must be at most 2' },
        { path: '/limits/max_steps', message: 'must be at least 1' },
        { path: '/limits/speed', message: 'unknown field' },
        { path: '/retry/retry_on', message: 'must be a list of at most 3 tool outcomes' },
      ]),
    )
  })

  it('reports a JSON syntax error at the root', () => {
    const { config, issues } = parseAgentConfig('{"agent": ')
    expect(config).toBeNull()
    expect(issues[0]!.path).toBe('/')
  })
})

describe('buildExpected', () => {
  const build = (draft: Parameters<typeof buildExpected>[0]) => {
    const issues: FieldIssue[] = []
    return { expected: buildExpected(draft, issues), issues }
  }

  it('keeps exact values type-strict: JSON in, JSON out', () => {
    expect(build({ field: 'answer', match: 'exact', value: '"1987"', tolerance: '' }).expected.value).toBe('1987')
    expect(build({ field: 'answer', match: 'exact', value: '1987', tolerance: '' }).expected.value).toBe(1987)
    expect(build({ field: 'answer', match: 'exact', value: '1987 AD', tolerance: '' }).issues[0]!.path).toBe('/expected/value')
  })

  it('builds numeric with a tolerance and one_of from lines', () => {
    expect(build({ field: 'total', match: 'numeric', value: '42.5', tolerance: '0.5' }).expected).toMatchObject({
      value: 42.5,
      tolerance: 0.5,
      values: null,
    })
    expect(build({ field: 'answer', match: 'one_of', value: 'September\n\n Sept ', tolerance: '' }).expected).toMatchObject({
      value: null,
      values: ['September', 'Sept'],
    })
    expect(build({ field: 'answer', match: 'numeric', value: 'many', tolerance: '' }).issues).toHaveLength(1)
  })

  it('round-trips a case expectation through the form', () => {
    const expected = { field: 'answer', match: 'exact' as const, value: 1987, values: null, tolerance: null, max_chars: null }
    expect(build(expectedDraft(expected)).expected).toEqual(expected)
  })

  it('keeps the max_chars bound of a contains expectation, so "Run this case" grades as strictly', () => {
    const expected = { field: 'answer', match: 'contains' as const, value: '09:00-19:00', values: null, tolerance: null, max_chars: 40 }
    expect(build(expectedDraft(expected)).expected).toEqual(expected)
    const draft = { ...expectedDraft(expected), maxChars: '0' }
    expect(build(draft).issues).toEqual([{ path: '/expected/max_chars', message: 'must be a whole number from 1 to 5000' }])
  })

  it('keeps non-string one_of values typed, since one_of compares them type-strictly', () => {
    const expected = { field: 'year', match: 'one_of' as const, value: null, values: [1987, 'nineteen eighty-seven'], tolerance: null, max_chars: null }
    const draft = expectedDraft(expected)
    expect(draft.valuesJson).toBe(true)
    expect(build(draft).expected).toEqual(expected)
    expect(build({ ...draft, value: '1987\nnot json' }).issues[0]!.path).toBe('/expected/values/1')
    // Plain-text lines stay strings when every prefilled value was a string.
    expect(expectedDraft({ ...expected, values: ['September', 'Sept'] }).valuesJson).toBe(false)
  })
})

describe('buildFaults', () => {
  it('maps blank call numbers to null (every call / every later call)', () => {
    const issues: FieldIssue[] = []
    const faults = buildFaults([{ tool: 'search_documents', kind: 'timeout', onCall: '', times: '', payload: ' ' }], issues)
    expect(faults).toEqual([{ tool: 'search_documents', kind: 'timeout', on_call: null, times: null, payload: null }])
    expect(issues).toEqual([])
  })
})
