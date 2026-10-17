import { describe, expect, it } from 'vitest'
import { ApiError, JobFailedError } from '../client'
import { describeError } from '../errors'
import { partialPurge } from '@/components/settings/purge'

describe('describeError', () => {
  it.each([
    [new TypeError('Failed to fetch'), 'unreachable'],
    [new ApiError(502, 'Bad Gateway'), 'unreachable'],
    [new ApiError(503, 'database revision 0001 is behind; run agentscope db upgrade'), 'migration'],
    [new ApiError(500, '(sqlite3.OperationalError) no such table: suite_runs'), 'migration'],
    [new ApiError(404, 'run not found'), 'not_found'],
    [new ApiError(422, 'task: too long'), 'other'],
    [new JobFailedError({ job_id: 'j', kind: 'run', state: 'failed', resource_id: 'r', created_at: '', finished_at: null, progress: 0, total: 0, error: 'boom' }), 'other'],
  ])('%s is %s', (error, kind) => {
    expect(describeError(error).kind).toBe(kind)
  })
})

describe('partialPurge', () => {
  const result = { deleted: { runs: 1 }, size_bytes_before: 10, size_bytes_after: 5 }

  it.each([
    ['detail.result', { detail: { message: 'again', result } }],
    ['result', { detail: 'again', result }],
    ['detail', { detail: result }],
  ])('finds the partial result under %s', (_where, body) => {
    expect(partialPurge(new ApiError(409, 'again', body))?.result).toEqual(result)
  })

  it('is null for anything but a 409', () => {
    expect(partialPurge(new ApiError(500, 'x', { detail: { result } }))).toBeNull()
    expect(partialPurge(new Error('x'))).toBeNull()
  })

  it('keeps the message when the body has no result', () => {
    expect(partialPurge(new ApiError(409, 'busy', { detail: 'busy' }))).toEqual({ message: 'busy', result: null })
  })
})
