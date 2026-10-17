import { ApiError } from '@/api/client'
import type { PurgeResult, PurgeScope } from '@/api/types'

export const SCOPE_TEXT: Record<PurgeScope, string> = {
  all: 'Everything: runs and traces, evaluations, comparisons and imported datasets.',
  runs: 'Runs and their traces. Evaluation verdicts are kept, unlinked from their runs.',
  evaluations: 'Evaluations with their case results and runs, and the comparisons built on them.',
  experiments: 'Comparisons only.',
  datasets: 'Imported dataset versions.',
}

function isPurgeResult(value: unknown): value is PurgeResult {
  return (
    value !== null &&
    typeof value === 'object' &&
    'deleted' in value &&
    typeof (value as { deleted: unknown }).deleted === 'object'
  )
}

export type PurgeConflict =
  | { kind: 'partial'; message: string; result: PurgeResult }
  | { kind: 'refused'; message: string }

/**
 * What a 409 from the purge endpoint means. With a PurgeResult in the body (under
 * `detail.result`, `result` or as `detail` itself), rows were deleted but their old contents
 * are still on disk (another connection kept SQLite from truncating its write-ahead log).
 * Without one, the purge was refused before anything was deleted (e.g. jobs still running):
 * calling that "partial" would tell the user data was deleted when none was.
 */
export function purgeConflict(error: unknown): PurgeConflict | null {
  if (!(error instanceof ApiError) || error.status !== 409) return null
  const body = error.body as { detail?: unknown; result?: unknown } | null
  const detail = body?.detail as { result?: unknown } | undefined
  const result = [detail?.result, body?.result, detail].find(isPurgeResult)
  return result ? { kind: 'partial', message: error.message, result } : { kind: 'refused', message: error.message }
}
