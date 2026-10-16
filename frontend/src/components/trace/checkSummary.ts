import type { CheckResult, JsonValue } from '@/api/types'
import { compact, plural } from '@/composables/format'

type JsonRecord = { [key: string]: JsonValue }

function isRecord(value: JsonValue): value is JsonRecord {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
}

function toolBounds(expected: JsonValue): string {
  if (!isRecord(expected)) return compact(expected)
  return Object.entries(expected)
    .map(([tool, bounds]) => {
      const [min, max] = Array.isArray(bounds) ? bounds : []
      if (typeof max === 'number') return `${tool} ${String(min)}–${max}×`
      return `${tool} ≥${String(min ?? 1)}×`
    })
    .join(', ')
}

function toolCounts(actual: JsonValue): string {
  if (!isRecord(actual)) return compact(actual)
  const parts = Object.entries(actual).map(([tool, n]) => `${tool} ${String(n)}×`)
  return parts.length ? parts.join(', ') : 'nothing'
}

function completionExpected(expected: JsonValue): string {
  if (!isRecord(expected)) return compact(expected)
  const parts: string[] = []
  if (expected.must_complete) parts.push('completed')
  if (typeof expected.expected_output_status === 'string') {
    parts.push(`status ${expected.expected_output_status}`)
  }
  if (typeof expected.max_steps_budget === 'number') parts.push(`≤${expected.max_steps_budget} steps`)
  if (typeof expected.max_tool_calls === 'number') parts.push(`≤${expected.max_tool_calls} tool calls`)
  return parts.join(', ') || 'any outcome'
}

function completionActual(actual: JsonValue): string {
  if (!isRecord(actual)) return compact(actual)
  const steps = typeof actual.steps === 'number' ? plural(actual.steps, 'step') : null
  const calls = typeof actual.tool_calls === 'number' ? plural(actual.tool_calls, 'tool call') : null
  return [actual.status, steps, calls].filter(Boolean).join(', ')
}

/** A one-line expected-vs-actual summary for a check row; the full values sit below it. */
export function expectedVsActual(check: CheckResult): string {
  if (check.status === 'not_applicable') return check.message
  switch (check.name) {
    case 'answer_correctness':
      return `expected ${compact(check.expected, 32)} · got ${check.actual === null ? 'no answer' : compact(check.actual, 32)}`
    case 'required_tool_calls':
      return `expected ${toolBounds(check.expected)} · executed ${toolCounts(check.actual)}`
    case 'task_completion':
      return `expected ${completionExpected(check.expected)} · got ${completionActual(check.actual)}`
    default:
      if (check.expected === null && check.actual === null) return check.message
      return `expected ${compact(check.expected, 32)} · got ${compact(check.actual, 32)}`
  }
}
