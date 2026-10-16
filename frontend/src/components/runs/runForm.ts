import type {
  AgentConfig,
  ExpectedAnswer,
  FaultKind,
  FaultSpec,
  JsonValue,
  MatchMode,
  ModelFaultKind,
  ModelFaultSpec,
} from '@/api/types'
import { parseJson, type FieldIssue } from '@/composables/parseJson'

export type { FieldIssue }

const IDENTIFIER = /^[a-z][a-z0-9_]{0,63}$/
const SLUG = /^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$/
const MODEL_NAME = /^[A-Za-z0-9._:/-]{1,128}$/
const FIELD_PATH = /^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z0-9_]+)*$/

type JsonObject = { [key: string]: unknown }

function isObject(value: unknown): value is JsonObject {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
}

interface NumberRule {
  min: number
  max: number
  integer?: boolean
  exclusiveMin?: boolean
  nullable?: boolean
}

/** Checks the fields that exist and flags unknown ones; missing optional fields take defaults. */
function checkObject(
  value: unknown,
  path: string,
  fields: Record<string, (value: unknown, path: string) => string | null>,
  issues: FieldIssue[],
): void {
  if (!isObject(value)) {
    issues.push({ path: path || '/', message: 'must be an object' })
    return
  }
  for (const [key, item] of Object.entries(value)) {
    const check = fields[key]
    if (!check) {
      issues.push({ path: `${path}/${key}`, message: 'unknown field' })
      continue
    }
    const message = check(item, `${path}/${key}`)
    if (message) issues.push({ path: `${path}/${key}`, message })
  }
}

const num =
  (rule: NumberRule) =>
  (value: unknown): string | null => {
    if (value === null && rule.nullable) return null
    if (typeof value !== 'number' || !Number.isFinite(value)) return 'must be a number'
    if (rule.integer && !Number.isInteger(value)) return 'must be an integer'
    if (rule.exclusiveMin ? value <= rule.min : value < rule.min) {
      return `must be ${rule.exclusiveMin ? 'greater than' : 'at least'} ${rule.min}`
    }
    if (value > rule.max) return `must be at most ${rule.max}`
    return null
  }

const text =
  (pattern?: RegExp, max = 2000) =>
  (value: unknown): string | null => {
    if (typeof value !== 'string') return 'must be a string'
    if (value.length > max) return `must be at most ${max} characters`
    if (pattern && !pattern.test(value)) return `does not match ${String(pattern)}`
    return null
  }

const oneOf =
  (values: readonly string[]) =>
  (value: unknown): string | null =>
    typeof value === 'string' && values.includes(value) ? null : `must be one of ${values.join(', ')}`

const bool = (value: unknown): string | null => (typeof value === 'boolean' ? null : 'must be true or false')

const RETRYABLE = ['ok', 'error', 'timeout', 'malformed', 'permission_denied', 'invalid_arguments', 'unknown_tool']

/**
 * Structural validation of a hand-written AgentConfig, mirroring domain/agents.py closely
 * enough to point at mistakes before anything is sent. The API stays the authority: it
 * re-validates, and also checks tool names against the registry.
 */
export function validateAgentConfig(value: unknown): FieldIssue[] {
  const issues: FieldIssue[] = []
  if (!isObject(value)) return [{ path: '/', message: 'must be a JSON object' }]
  for (const key of ['agent', 'version', 'prompt_version', 'tools']) {
    if (!(key in value)) issues.push({ path: `/${key}`, message: 'is required' })
  }
  checkObject(
    value,
    '',
    {
      agent: text(IDENTIFIER),
      version: text(SLUG),
      label: text(undefined, 200),
      description: text(undefined, 2000),
      prompt_version: text(SLUG),
      model: (model, path) => {
        checkObject(
          model,
          path,
          {
            provider: oneOf(['fake', 'ollama', 'openai_compatible']),
            model: text(MODEL_NAME, 128),
            temperature: num({ min: 0, max: 2 }),
            seed: num({ min: -(2 ** 63), max: 2 ** 63, integer: true, nullable: true }),
            max_output_tokens: num({ min: 16, max: 4096, integer: true }),
            request_timeout_s: num({ min: 0, max: 600, exclusiveMin: true }),
          },
          issues,
        )
        return null
      },
      tools: (tools, path) => {
        if (!Array.isArray(tools)) return 'must be a list of tool names'
        if (tools.length > 32) return 'must list at most 32 tools'
        tools.forEach((tool, i) => {
          const message = text(IDENTIFIER, 64)(tool)
          if (message) issues.push({ path: `${path}/${i}`, message })
        })
        return null
      },
      tool_schema_version: num({ min: 1, max: 100, integer: true }),
      graph: oneOf(['basic', 'validated']),
      isolate_untrusted: bool,
      retry: (retry, path) => {
        checkObject(
          retry,
          path,
          {
            max_tool_retries: num({ min: 0, max: 3, integer: true }),
            retry_on: (outcomes) =>
              Array.isArray(outcomes) && outcomes.length <= 3 && outcomes.every((o) => oneOf(RETRYABLE)(o) === null)
                ? null
                : 'must be a list of at most 3 tool outcomes',
            max_decision_repairs: num({ min: 0, max: 3, integer: true }),
            max_output_repairs: num({ min: 0, max: 3, integer: true }),
          },
          issues,
        )
        return null
      },
      limits: (limits, path) => {
        checkObject(
          limits,
          path,
          {
            max_steps: num({ min: 1, max: 50, integer: true }),
            tool_timeout_s: num({ min: 0, max: 30, exclusiveMin: true }),
            run_timeout_s: num({ min: 0, max: 600, exclusiveMin: true }),
            max_tool_output_chars: num({ min: 256, max: 200_000, integer: true }),
          },
          issues,
        )
        return null
      },
    },
    issues,
  )
  return issues
}

export function parseAgentConfig(source: string): { config: AgentConfig | null; issues: FieldIssue[] } {
  const parsed = parseJson(source)
  if (parsed.issues.length) return { config: null, issues: parsed.issues }
  const issues = validateAgentConfig(parsed.value)
  return { config: issues.length ? null : (parsed.value as AgentConfig), issues }
}

// --- faults ----------------------------------------------------------------------------

/** A fault row as edited in the form: blank numbers mean "every call" / "all later calls". */
export interface FaultDraft {
  tool: string
  kind: FaultKind
  onCall: string
  times: string
  payload: string
}

export interface ModelFaultDraft {
  kind: ModelFaultKind
  onCall: string
  times: string
}

export function faultDraft(spec: FaultSpec): FaultDraft {
  return {
    tool: spec.tool,
    kind: spec.kind,
    onCall: spec.on_call === null ? '' : String(spec.on_call),
    times: spec.times === null ? '' : String(spec.times),
    payload: spec.payload ?? '',
  }
}

export function modelFaultDraft(spec: ModelFaultSpec): ModelFaultDraft {
  return { kind: spec.kind, onCall: String(spec.on_call), times: String(spec.times) }
}

function callNumber(raw: string, path: string, issues: FieldIssue[], blankAllowed: boolean): number | null {
  const trimmed = raw.trim()
  if (trimmed === '') {
    if (!blankAllowed) issues.push({ path, message: 'is required' })
    return null
  }
  const value = Number(trimmed)
  if (!Number.isInteger(value) || value < 1 || value > 50) {
    issues.push({ path, message: 'must be a whole number from 1 to 50' })
    return null
  }
  return value
}

export function buildFaults(drafts: FaultDraft[], issues: FieldIssue[]): FaultSpec[] {
  return drafts.map((draft, i) => {
    if (!IDENTIFIER.test(draft.tool)) issues.push({ path: `/faults/${i}/tool`, message: 'choose a tool' })
    return {
      tool: draft.tool,
      kind: draft.kind,
      on_call: callNumber(draft.onCall, `/faults/${i}/on_call`, issues, true),
      times: callNumber(draft.times, `/faults/${i}/times`, issues, true),
      payload: draft.payload.trim() === '' ? null : draft.payload,
    }
  })
}

export function buildModelFaults(drafts: ModelFaultDraft[], issues: FieldIssue[]): ModelFaultSpec[] {
  return drafts.map((draft, i) => ({
    kind: draft.kind,
    on_call: callNumber(draft.onCall, `/model_faults/${i}/on_call`, issues, false) ?? 1,
    times: callNumber(draft.times, `/model_faults/${i}/times`, issues, false) ?? 1,
  }))
}

// --- expected answer ---------------------------------------------------------------------

export interface ExpectedDraft {
  field: string
  match: MatchMode
  /** The value as typed: JSON for exact (type-strict), one accepted value per line for one_of. */
  value: string
  tolerance: string
}

function scalarText(value: JsonValue): string {
  return typeof value === 'string' ? value : JSON.stringify(value)
}

export function expectedDraft(expected: ExpectedAnswer): ExpectedDraft {
  let value: string
  if (expected.match === 'one_of') value = (expected.values ?? []).map(scalarText).join('\n')
  else if (expected.match === 'exact') value = JSON.stringify(expected.value)
  else value = scalarText(expected.value)
  return {
    field: expected.field,
    match: expected.match,
    value,
    tolerance: expected.tolerance === null ? '' : String(expected.tolerance),
  }
}

/** The ExpectedAnswer the form describes, with the same consistency rules as the domain. */
export function buildExpected(draft: ExpectedDraft, issues: FieldIssue[]): ExpectedAnswer {
  const expected: ExpectedAnswer = {
    field: draft.field.trim(),
    match: draft.match,
    value: null,
    values: null,
    tolerance: null,
    max_chars: null,
  }
  if (!FIELD_PATH.test(expected.field)) {
    issues.push({ path: '/expected/field', message: 'must be a dotted field name such as answer' })
  }
  if (draft.match === 'one_of') {
    const values = draft.value
      .split('\n')
      .map((line) => line.trim())
      .filter(Boolean)
    if (!values.length) issues.push({ path: '/expected/values', message: 'list at least one accepted value' })
    expected.values = values
  } else if (draft.match === 'numeric') {
    const value = Number(draft.value.trim())
    if (draft.value.trim() === '' || !Number.isFinite(value)) {
      issues.push({ path: '/expected/value', message: 'must be a number' })
    }
    expected.value = value
    if (draft.tolerance.trim() !== '') {
      const tolerance = Number(draft.tolerance.trim())
      if (!Number.isFinite(tolerance) || tolerance < 0) {
        issues.push({ path: '/expected/tolerance', message: 'must be a number of at least 0' })
      }
      expected.tolerance = tolerance
    }
  } else if (draft.match === 'exact') {
    // Exact equality is type-strict, so the value is JSON: "1987" is a string, 1987 a number.
    const parsed = parseJson(draft.value)
    if (draft.value.trim() === '' || parsed.issues.length) {
      issues.push({ path: '/expected/value', message: 'must be JSON, e.g. "1987" for a string or 1987 for a number' })
    } else {
      expected.value = parsed.value as JsonValue
    }
  } else {
    if (draft.value === '') issues.push({ path: '/expected/value', message: 'is required' })
    expected.value = draft.value
  }
  return expected
}
