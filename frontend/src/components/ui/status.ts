import type { Outcome, Tone } from '@/composables/useTraceModel'
import type { IconName } from './icons'

const TONES: Record<string, Tone> = {
  completed: 'ok',
  passed: 'ok',
  succeeded: 'ok',
  ok: 'ok',
  failed: 'fail',
  error: 'fail',
  timeout: 'fail',
  max_steps: 'fail',
  running: 'neutral',
  queued: 'neutral',
  not_applicable: 'na',
}

/** Every tone has its own glyph shape, so status never depends on colour alone. */
export const TONE_ICONS: Record<Tone, IconName> = {
  ok: 'check',
  fail: 'x',
  warn: 'alert',
  na: 'minus',
  neutral: 'dot',
}

/** Maps a run, check, job or span status string to a tone and a human label. */
export function statusOutcome(status: string): Outcome {
  return { tone: TONES[status] ?? 'na', label: status.replace(/_/g, ' ') }
}
