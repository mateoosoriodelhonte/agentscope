import type { EvalCase, ExpectedAnswer, JsonValue } from '@/api/types'
import { compact, plural } from '@/composables/format'

function value(v: JsonValue): string {
  return compact(v, 40)
}

function answer(expected: ExpectedAnswer): string {
  const field = expected.field
  switch (expected.match) {
    case 'one_of':
      return `${field} one of ${(expected.values ?? []).map(value).join(' | ')}`
    case 'numeric':
      return `${field} ≈ ${value(expected.value)}${expected.tolerance ? ` ±${expected.tolerance}` : ''}`
    case 'contains':
      return `${field} contains ${value(expected.value)}`
    default:
      return `${field} ${expected.match} ${value(expected.value)}`
  }
}

/** Short phrases describing what a case expects, in the order the checks consume them. */
export function expectationSummary(c: EvalCase): string[] {
  const out: string[] = []
  if (c.expected) out.push(answer(c.expected))
  if (c.expected_tool_calls.length) {
    const calls = c.expected_tool_calls.map((call) => {
      const bounds = call.max_calls === null ? `≥${call.min_calls}` : `${call.min_calls}–${call.max_calls}`
      return `${call.tool} ${bounds}×`
    })
    out.push(`calls ${calls.join(', ')}`)
  }
  if (c.tool_order.length) out.push(`order ${c.tool_order.join(' → ')}${c.tool_order_strict ? ' (strict)' : ''}`)
  if (c.forbidden_tools.length) out.push(`forbids ${c.forbidden_tools.join(', ')}`)
  if (c.forbidden_output_substrings.length) {
    out.push(`must not leak ${plural(c.forbidden_output_substrings.length, 'string')}`)
  }
  const sc = c.success_criteria
  if (!sc.must_complete) out.push('may stop early')
  if (sc.expected_output_status) out.push(`outcome ${sc.expected_output_status.replace(/_/g, ' ')}`)
  if (sc.expect_recovery) out.push('must recover')
  if (sc.max_steps_budget !== null) out.push(`≤${sc.max_steps_budget} steps`)
  if (sc.max_tool_calls !== null) out.push(`≤${sc.max_tool_calls} tool calls`)
  return out
}

/** Injected faults as labels, tool faults first. */
export function faultLabels(c: EvalCase): string[] {
  return [...c.faults.map((f) => `${f.tool}: ${f.kind}`), ...c.model_faults.map((f) => `model: ${f.kind}`)]
}
