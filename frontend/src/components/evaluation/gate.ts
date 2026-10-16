import type { GateRule, JsonValue } from '@/api/types'
import { compact, formatPercent } from '@/composables/format'
import type { Outcome } from '@/composables/useTraceModel'

export function gateOutcome(passed: boolean | null | undefined): Outcome {
  if (passed === true) return { tone: 'ok', label: 'gate passed' }
  if (passed === false) return { tone: 'fail', label: 'gate failed' }
  return { tone: 'na', label: 'no gate' }
}

const LABELS: Record<string, string> = {
  min_pass_rate: 'Pass rate',
  max_new_regressions: 'New regressions vs baseline',
  max_pass_rate_drop_pp: 'Pass-rate drop vs baseline',
  max_invalid_outputs: 'Invalid outputs',
  max_policy_violation_cases: 'Cases with policy violations',
  max_unauthorized_tool_executions: 'Unauthorized tool executions',
  max_harness_errors: 'Harness errors',
}

const REQUIRED = 'required_check:'

export function ruleLabel(rule: GateRule): string {
  if (rule.name.startsWith(REQUIRED)) return `Required check ${rule.name.slice(REQUIRED.length)}`
  return LABELS[rule.name] ?? rule.name
}

function isRecord(value: JsonValue): value is { [key: string]: JsonValue } {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
}

/** The threshold as a person would state it ("≥ 80% of cases", "≤ 0"). */
export function formatThreshold(rule: GateRule): string {
  const t = rule.threshold
  if (rule.name === 'min_pass_rate' && typeof t === 'number') return `≥ ${formatPercent(t)} of cases`
  if (rule.name === 'max_pass_rate_drop_pp' && typeof t === 'number') return `≤ ${t} pp`
  if (rule.name.startsWith(REQUIRED) && isRecord(t)) {
    return `${String(t.max_failed ?? 0)} failures, applies to ≥ ${String(t.min_applicable ?? 1)} case`
  }
  if (rule.name.startsWith('max_') && typeof t === 'number') return `≤ ${t}`
  return compact(t)
}

/** The measured value; null means it could not be measured (e.g. no baseline). */
export function formatActual(rule: GateRule): string {
  const a = rule.actual
  if (a === null) return 'not measured'
  if (rule.name === 'max_pass_rate_drop_pp' && typeof a === 'number') return `${a} pp`
  if (rule.name.startsWith(REQUIRED) && isRecord(a)) {
    return `${String(a.failed)} failed of ${String(a.applicable)} applicable`
  }
  return typeof a === 'string' ? a : compact(a)
}
