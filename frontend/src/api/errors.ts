import { ApiError, JobFailedError } from './client'
import type { ValidationIssue } from './types'

/**
 * What went wrong, in the terms an empty state needs: each kind has its own fix.
 * - unreachable: no API answered (fetch failed, or a proxy reported the upstream down);
 * - migration: the API answered but its database schema is behind the code;
 * - not_found: the resource does not exist (it may have been purged);
 * - other: anything else, shown with the server's message.
 */
export type ErrorKind = 'unreachable' | 'migration' | 'not_found' | 'other'

export interface DescribedError {
  kind: ErrorKind
  status: number | null
  message: string
  /** Why a request was refused, by location (a 422's `issues`); empty when not given. */
  issues: ValidationIssue[]
}

const MIGRATION = /db upgrade|migration|database revision|no such table|undefinedtable/i
const UPSTREAM_DOWN = new Set([502, 503, 504])

export function describeError(error: unknown): DescribedError {
  const message = error instanceof Error ? error.message : String(error)
  if (error instanceof ApiError) {
    const { status, issues } = error
    if (MIGRATION.test(message)) return { kind: 'migration', status, message, issues }
    if (status === 404) return { kind: 'not_found', status, message, issues }
    if (UPSTREAM_DOWN.has(status)) return { kind: 'unreachable', status, message, issues }
    return { kind: 'other', status, message, issues }
  }
  if (error instanceof JobFailedError) return { kind: 'other', status: null, message, issues: [] }
  // fetch() rejects with a TypeError when nothing answers at all.
  if (error instanceof TypeError) return { kind: 'unreachable', status: null, message, issues: [] }
  return { kind: 'other', status: null, message, issues: [] }
}

/** True for an aborted request: not an error worth showing. */
export function isAbort(error: unknown): boolean {
  return error instanceof DOMException && error.name === 'AbortError'
}
