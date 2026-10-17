import { describe, expect, it } from 'vitest'
import { ApiError, JobFailedError } from '../client'
import { describeError } from '../errors'
import { purgeConflict } from '@/components/settings/purge'

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

  it('carries the located issues of a refused request', () => {
    const issue = { path: '/cases/0/agent', message: 'does not match the agent', severity: 'error' }
    expect(describeError(new ApiError(422, 'cannot be evaluated', { detail: 'x', issues: [issue] })).issues).toEqual([issue])
    expect(describeError(new TypeError('Failed to fetch')).issues).toEqual([])
  })
})

describe('purgeConflict', () => {
  const result = { deleted: { runs: 1 }, size_bytes_before: 10, size_bytes_after: 5 }

  it.each([
    ['detail.result', { detail: { message: 'again', result } }],
    ['result', { detail: 'again', result }],
    ['detail', { detail: result }],
  ])('finds the partial result under %s', (_where, body) => {
    expect(purgeConflict(new ApiError(409, 'again', body))).toEqual({ kind: 'partial', message: 'again', result })
  })

  it('is null for anything but a 409', () => {
    expect(purgeConflict(new ApiError(500, 'x', { detail: { result } }))).toBeNull()
    expect(purgeConflict(new Error('x'))).toBeNull()
  })

  it('treats a 409 without a result as a refusal: nothing was deleted', () => {
    expect(purgeConflict(new ApiError(409, 'busy', { detail: 'busy', code: 'jobs_running' }))).toEqual({
      kind: 'refused',
      message: 'busy',
    })
    expect(purgeConflict(new ApiError(409, 'busy', null))).toEqual({ kind: 'refused', message: 'busy' })
  })
})
