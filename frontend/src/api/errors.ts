import { ApiError, JobFailedError } from './client'

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
}

const MIGRATION = /db upgrade|migration|database revision|no such table|undefinedtable/i
const UPSTREAM_DOWN = new Set([502, 503, 504])

export function describeError(error: unknown): DescribedError {
  const message = error instanceof Error ? error.message : String(error)
  if (error instanceof ApiError) {
    if (MIGRATION.test(message)) return { kind: 'migration', status: error.status, message }
    if (error.status === 404) return { kind: 'not_found', status: 404, message }
    if (UPSTREAM_DOWN.has(error.status)) return { kind: 'unreachable', status: error.status, message }
    return { kind: 'other', status: error.status, message }
  }
  if (error instanceof JobFailedError) return { kind: 'other', status: null, message }
  // fetch() rejects with a TypeError when nothing answers at all.
  if (error instanceof TypeError) return { kind: 'unreachable', status: null, message }
  return { kind: 'other', status: null, message }
}

/** True for an aborted request: not an error worth showing. */
export function isAbort(error: unknown): boolean {
  return error instanceof DOMException && error.name === 'AbortError'
}
