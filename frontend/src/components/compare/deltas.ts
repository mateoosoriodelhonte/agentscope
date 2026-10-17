import type { MetricDelta } from '@/api/types'
import { formatPercent, formatPp, formatSigned } from '@/composables/format'
import type { Outcome } from '@/composables/useTraceModel'

export const CHECK_PREFIX = 'check:'

const LABELS: Record<string, string> = {
  pass_rate: 'Pass rate',
  passed: 'Passed cases',
  harness_errors: 'Harness errors',
  invalid_outputs: 'Invalid outputs',
  policy_violation_cases: 'Cases with policy violations',
  unauthorized_tool_attempts: 'Unauthorized attempts',
  unauthorized_tool_executions: 'Unauthorized executions',
  unexpected_tool_errors: 'Unexpected tool errors',
  avg_steps: 'Average steps',
}

export function metricLabel(delta: MetricDelta): string {
  if (delta.metric.startsWith(CHECK_PREFIX)) return delta.metric.slice(CHECK_PREFIX.length)
  return LABELS[delta.metric] ?? delta.metric.replace(/_/g, ' ')
}

/** One side's value in the metric's unit: pp rows hold a 0–100 rate. */
export function formatSide(delta: MetricDelta, value: number): string {
  if (delta.unit === 'pp') return formatPercent(value / 100)
  if (delta.unit === 'steps') return value.toFixed(1)
  return String(value)
}

/** The difference B − A: percentage points or an absolute count, never a relative %. */
export function formatDelta(delta: MetricDelta): string {
  if (delta.unit === 'pp') return formatPp(delta.delta)
  return formatSigned(delta.delta, delta.unit === 'steps' ? 1 : 0)
}

/** Whether B is better than A, knowing which direction is good for this metric. */
export function direction(delta: MetricDelta): Outcome {
  if (Number(delta.delta.toFixed(4)) === 0) return { tone: 'na', label: 'no change' }
  return delta.delta > 0 === delta.higher_is_better ? { tone: 'ok', label: 'better' } : { tone: 'fail', label: 'worse' }
}
