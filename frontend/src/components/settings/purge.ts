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

/**
 * A 409 from the purge endpoint means rows were deleted but their old contents are still on
 * disk (another connection kept SQLite from truncating its write-ahead log). The body carries
 * the partial result, under `detail.result`, `result` or as `detail` itself.
 */
export function partialPurge(error: unknown): { message: string; result: PurgeResult | null } | null {
  if (!(error instanceof ApiError) || error.status !== 409) return null
  const body = error.body as { detail?: unknown; result?: unknown } | null
  const detail = body?.detail as { result?: unknown } | undefined
  const candidates = [detail?.result, body?.result, detail]
  return { message: error.message, result: candidates.find(isPurgeResult) ?? null }
}
