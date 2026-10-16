import type {
  JsonValue,
  RunStatus,
  SpanKind,
  SpanRecord,
  StateTransition,
  ToolInvocation,
  TraceRecord,
} from '@/api/types'
import { plural, truncate } from './format'

/** Span attribute names written by src/agentscope/tracing/semconv.py. */
export const ATTR = {
  node: 'agentscope.node',
  route: 'agentscope.route',
  decision: 'agentscope.decision',
  toolName: 'gen_ai.tool.name',
  callId: 'gen_ai.tool.call.id',
  attempt: 'agentscope.tool.attempt',
  toolOutcome: 'agentscope.tool.outcome',
  fault: 'agentscope.fault.kind',
  modelFault: 'agentscope.model_fault.kind',
  allowed: 'agentscope.guardrail.allowed',
  findings: 'agentscope.guardrail.findings',
  delimited: 'agentscope.observation.delimited',
  outputValid: 'agentscope.output.valid',
  runStatus: 'agentscope.run.status',
  cancelled: 'agentscope.cancelled',
  truncated: 'agentscope.payload.truncated',
} as const

export const SIMULATED_PROVIDER = 'agentscope.fake'

export type Tone = 'ok' | 'fail' | 'warn' | 'na' | 'neutral'

export interface Outcome {
  tone: Tone
  label: string
}

export type RowKind = SpanKind | 'step' | 'finish'

export interface TraceRow {
  /** A span id, or `step-N` / `finish` for the synthetic group rows. */
  id: string
  level: 1 | 2 | 3
  parentId: string | null
  /** The top-level row this row belongs to (itself for top-level rows). */
  groupId: string
  kind: RowKind
  span: SpanRecord | null
  label: string
  /** One-line summary for top-level rows, built from structured trace data. */
  narrative: string | null
  outcome: Outcome
  /** Offset from the run start, in milliseconds. */
  startMs: number
  durationMs: number
  childIds: string[]
  /** Injected tool or model fault kind recorded on this span. */
  fault: string | null
}

export type ChipKind = 'denied' | 'retry' | 'repair' | 'decision_repair' | 'terminal'

/** A transition that leaves the normal plan → guard → act → observe loop. */
export interface ReasonChip {
  kind: ChipKind
  label: string
  title: string
  tone: Tone
}

export interface StepGroup {
  id: string
  kind: 'step' | 'finish'
  /** 1-based step number; 0 for the finish group. */
  index: number
  label: string
  narrative: string
  outcome: Outcome
  chips: ReasonChip[]
  transitions: StateTransition[]
  hasFault: boolean
}

export interface TraceModel {
  rows: TraceRow[]
  rowById: Map<string, TraceRow>
  groups: StepGroup[]
  runRowId: string | null
  totalMs: number
  /** Longest row below the run root: the scale of the relative duration bars. */
  relativeScaleMs: number
  toolTimeoutMs: number
  simulated: boolean
}

export function attr(span: SpanRecord | null | undefined, key: string): JsonValue | undefined {
  return span?.attributes[key]
}

export function attrString(span: SpanRecord | null | undefined, key: string): string | null {
  const value = attr(span, key)
  return typeof value === 'string' ? value : null
}

function attrNumber(span: SpanRecord | null | undefined, key: string): number | null {
  const value = attr(span, key)
  return typeof value === 'number' ? value : null
}

export function nodeName(span: SpanRecord): string {
  return attrString(span, ATTR.node) ?? span.name.replace(/^node /, '')
}

/** Epoch milliseconds with the sub-millisecond precision of the ISO string kept. */
export function epochMs(iso: string): number {
  const fraction = /\.(\d+)/.exec(iso)
  const base = Date.parse(fraction ? iso.replace(fraction[0], '') : iso)
  return fraction ? base + Number(`0.${fraction[1]}`) * 1000 : base
}

export interface ParsedDecision {
  raw: string
  value: { [key: string]: JsonValue } | null
}

/** The model's raw response on a chat span and, when it is a JSON object, its parsed form. */
export function decisionOf(chat: SpanRecord | null | undefined): ParsedDecision | null {
  const output = chat?.output
  if (!output || typeof output !== 'object' || Array.isArray(output)) return null
  const raw = output.content
  if (typeof raw !== 'string') return null
  try {
    const value: unknown = JSON.parse(raw)
    const isObject = value !== null && typeof value === 'object' && !Array.isArray(value)
    return { raw, value: isObject ? (value as { [key: string]: JsonValue }) : null }
  } catch {
    return { raw, value: null }
  }
}

export function runStatusOutcome(status: RunStatus | string): Outcome {
  switch (status) {
    case 'completed':
      return { tone: 'ok', label: 'completed' }
    case 'running':
      return { tone: 'neutral', label: 'running' }
    case 'max_steps':
      return { tone: 'fail', label: 'max steps' }
    case 'failed':
    case 'timeout':
    case 'error':
      return { tone: 'fail', label: status }
    default:
      return { tone: 'na', label: status }
  }
}

export function toolOutcome(outcome: string): Outcome {
  if (outcome === 'ok') return { tone: 'ok', label: 'ok' }
  if (outcome === 'timeout' || outcome === 'error' || outcome === 'malformed') {
    return { tone: 'fail', label: outcome }
  }
  return { tone: 'warn', label: outcome.replace(/_/g, ' ') }
}

const DENIAL_LABELS: Record<string, string> = {
  permission_denied: 'not permitted',
  unknown_tool: 'unknown tool',
  invalid_arguments: 'invalid arguments',
}

function resultCount(output: JsonValue): number | null {
  if (output && typeof output === 'object' && !Array.isArray(output)) {
    const results = output.results
    if (Array.isArray(results)) return results.length
  }
  return null
}

function toolSummary(inv: ToolInvocation): { text: string; outcome: Outcome } {
  if (!inv.executed) {
    const why = DENIAL_LABELS[inv.outcome] ?? inv.denial_reason ?? inv.outcome
    return { text: `Blocked ${inv.tool}: ${why}`, outcome: { tone: 'warn', label: 'blocked' } }
  }
  let text = `Called ${inv.tool} → ${inv.outcome}`
  if (inv.attempts.length > 1) text += ` after ${inv.attempts.length} attempts`
  const count = inv.outcome === 'ok' ? resultCount(inv.output) : null
  if (count !== null) text += ` (${plural(count, 'result')})`
  return { text, outcome: toolOutcome(inv.outcome) }
}

function answerText(chat: SpanRecord | undefined): string {
  const output = decisionOf(chat)?.value?.output
  if (output && typeof output === 'object' && !Array.isArray(output)) {
    if (typeof output.answer === 'string') return truncate(output.answer, 80)
    if (typeof output.status === 'string') return `${output.status} (no answer)`
  }
  return 'output produced'
}

function spanOutcome(span: SpanRecord, trace: TraceRecord, children: SpanRecord[]): Outcome {
  if (attr(span, ATTR.cancelled) === true) return { tone: 'fail', label: 'cancelled' }
  switch (span.kind) {
    case 'run':
      return runStatusOutcome(trace.status)
    case 'llm':
      if (span.status === 'error') return { tone: 'fail', label: 'error' }
      if (attrString(span, ATTR.modelFault)) return { tone: 'warn', label: 'fault injected' }
      return { tone: 'ok', label: 'ok' }
    case 'tool':
      return toolOutcome(attrString(span, ATTR.toolOutcome) ?? (span.status === 'ok' ? 'ok' : 'error'))
    case 'guardrail': {
      if (attr(span, ATTR.allowed) === false) return { tone: 'warn', label: 'denied' }
      const findings = attrNumber(span, ATTR.findings)
      if (findings) return { tone: 'warn', label: plural(findings, 'finding') }
      return { tone: 'ok', label: 'passed' }
    }
    case 'node':
      break
  }
  if (span.status === 'error') return { tone: 'fail', label: 'error' }
  const route = attrString(span, ATTR.route)
  switch (nodeName(span)) {
    case 'plan': {
      const decision = attrString(span, ATTR.decision)
      if (decision) return { tone: 'neutral', label: decision.replace('_', ' ') }
      return { tone: 'neutral', label: route === 'finish' ? 'stop' : `→ ${route ?? '?'}` }
    }
    case 'guard':
      return route === 'act' ? { tone: 'ok', label: 'permitted' } : { tone: 'warn', label: 'denied' }
    case 'act': {
      const attempts = children.filter((c) => c.kind === 'tool')
      const last = attempts[attempts.length - 1]
      return last ? spanOutcome(last, trace, []) : { tone: 'na', label: 'no attempt' }
    }
    case 'observe':
      return { tone: 'neutral', label: attr(span, ATTR.delimited) ? 'delimited' : 'not delimited' }
    case 'validate':
      return attr(span, ATTR.outputValid) === false
        ? { tone: 'fail', label: 'invalid' }
        : { tone: 'ok', label: 'valid' }
    case 'finish':
      return runStatusOutcome(attrString(span, ATTR.runStatus) ?? trace.status)
    default:
      return span.status === 'ok' ? { tone: 'ok', label: 'ok' } : { tone: 'na', label: span.status }
  }
}

interface Group {
  id: string
  kind: 'step' | 'finish'
  index: number
  nodes: SpanRecord[]
}

/**
 * Builds the step-first view of a trace: top-level rows are steps (one model decision and
 * what the runtime did with it), then a finish group; each expands to node spans and their
 * chat, tool and guardrail spans. Pure: no reactivity, no I/O.
 */
export function useTraceModel(trace: TraceRecord): TraceModel {
  const spans = trace.spans
  const root = spans.find((s) => s.kind === 'run') ?? spans.find((s) => !s.parent_span_id) ?? null
  const runStart = epochMs(root?.start_time ?? trace.started_at)
  const totalMs = root?.duration_ms ?? trace.duration_ms
  const children = new Map<string, SpanRecord[]>()
  for (const span of spans) {
    if (!span.parent_span_id) continue
    const list = children.get(span.parent_span_id) ?? []
    list.push(span)
    children.set(span.parent_span_id, list)
  }
  for (const list of children.values()) list.sort((a, b) => epochMs(a.start_time) - epochMs(b.start_time))
  const childrenOf = (span: SpanRecord) => children.get(span.span_id) ?? []
  const firstLlm = (span: SpanRecord | undefined) =>
    span ? childrenOf(span).find((c) => c.kind === 'llm') : undefined

  // A plan node that called the model starts a step. A plan node without a model call is
  // the step-budget check (or was cut short by the run deadline), so it belongs to finish.
  const groups: Group[] = []
  const finish: Group = { id: 'finish', kind: 'finish', index: 0, nodes: [] }
  let current: Group | null = null
  for (const node of root ? childrenOf(root) : []) {
    const name = node.kind === 'node' ? nodeName(node) : node.name
    const startsStep = name === 'plan' && firstLlm(node) !== undefined
    if (name === 'finish' || (name === 'plan' && !startsStep)) {
      finish.nodes.push(node)
      continue
    }
    if (startsStep || current === null) {
      current = { id: `step-${groups.length + 1}`, kind: 'step', index: groups.length + 1, nodes: [] }
      groups.push(current)
    }
    current.nodes.push(node)
  }
  groups.push(finish)

  const retry = trace.config.retry
  let outputRepairs = 0
  let decisionRepairs = 0
  const stepGroups: StepGroup[] = groups.map((group) => {
    const transitions = trace.transitions.filter((t) =>
      group.kind === 'finish'
        ? t.source === 'finish' || (t.target === 'END' && t.source !== 'finish')
        : t.step === group.index && t.source !== 'finish' && !(t.target === 'END'),
    )
    const all = group.nodes.flatMap((n) => [n, ...childrenOf(n)])
    const hasFault = all.some((s) => attrString(s, ATTR.fault) ?? attrString(s, ATTR.modelFault))
    const chips: ReasonChip[] = []
    let narrative: string
    let outcome: Outcome

    if (group.kind === 'finish') {
      outcome = runStatusOutcome(trace.status)
      narrative =
        trace.status === 'completed'
          ? `Completed in ${plural(trace.steps, 'step')}`
          : `${outcome.label[0]!.toUpperCase()}${outcome.label.slice(1)}: ${trace.error ?? trace.error_code ?? 'no detail recorded'}`
      if (trace.status !== 'completed' && trace.status !== 'running') {
        chips.push({ kind: 'terminal', label: outcome.label, title: narrative, tone: 'fail' })
      }
    } else {
      const plan = group.nodes.find((n) => nodeName(n) === 'plan')
      const chat = firstLlm(plan)
      const invocation = trace.tool_invocations.find((i) => i.step === group.index)
      const validates = group.nodes.filter((n) => nodeName(n) === 'validate')
      const invalidDecision = trace.guardrail_events.find(
        (g) => g.kind === 'invalid_decision' && g.step === group.index,
      )
      const decision = attrString(plan, ATTR.decision)

      for (const t of transitions) {
        if (t.source === 'guard' && t.target === 'observe') {
          chips.push({ kind: 'denied', label: `denied ${invocation?.tool ?? 'call'}`, title: t.reason, tone: 'warn' })
        } else if (t.source === 'validate' && t.target === 'plan') {
          outputRepairs += 1
          chips.push({
            kind: 'repair',
            label: `repair ${outputRepairs}/${retry.max_output_repairs}`,
            title: t.reason,
            tone: 'warn',
          })
        } else if (t.source === 'plan' && t.target === 'plan') {
          decisionRepairs += 1
          chips.push({
            kind: 'decision_repair',
            label: `decision repair ${decisionRepairs}/${retry.max_decision_repairs}`,
            title: t.reason,
            tone: 'warn',
          })
        }
      }
      if (invocation && invocation.attempts.length > 1) {
        const retries = invocation.attempts.length - 1
        chips.push({
          kind: 'retry',
          label: `retry ×${retries}`,
          title: invocation.attempts.map((a) => `attempt ${a.attempt}: ${a.outcome}`).join(', '),
          tone: 'warn',
        })
      }

      if (chat?.status === 'error') {
        narrative = `Model call failed: ${chat.status_message ?? 'no detail recorded'}`
        outcome = { tone: 'fail', label: 'error' }
      } else if (invalidDecision) {
        const kind = invalidDecision.detail.split(':')[0]
        const exhausted = attrString(plan, ATTR.route) === 'finish'
        narrative = `Invalid decision (${kind}) → ${exhausted ? 'repair budget exhausted' : 'asked to correct'}`
        outcome = { tone: 'fail', label: 'invalid' }
      } else if (decision === 'tool_call') {
        const tool = attrString(plan, ATTR.toolName) ?? 'a tool'
        const summary = invocation ? toolSummary(invocation) : null
        narrative = summary?.text ?? `Requested ${tool}`
        outcome = summary?.outcome ?? { tone: 'neutral', label: 'requested' }
      } else if (decision === 'final') {
        const rejected = validates.find((v) => attr(v, ATTR.outputValid) === false)
        if (rejected) {
          const repaired = attrString(rejected, ATTR.route) === 'plan'
          narrative = `Final answer rejected: schema invalid → ${repaired ? `repair ${outputRepairs}/${retry.max_output_repairs}` : 'repairs exhausted'}`
          outcome = { tone: 'fail', label: 'rejected' }
        } else {
          narrative = `Final answer: ${answerText(chat)}`
          outcome = { tone: 'ok', label: 'final' }
        }
      } else {
        narrative = 'Model call'
        outcome = { tone: 'neutral', label: 'pending' }
      }
      const modelFault = attrString(chat, ATTR.modelFault)
      if (modelFault) narrative += ` · injected ${modelFault}`
      if (all.some((s) => attr(s, ATTR.cancelled) === true)) {
        narrative += ' · cancelled at the run deadline'
        outcome = { tone: 'fail', label: 'cancelled' }
      }
    }
    return {
      id: group.id,
      kind: group.kind,
      index: group.index,
      label: group.kind === 'finish' ? 'Finish' : `Step ${group.index}`,
      narrative,
      outcome,
      chips,
      transitions,
      hasFault: Boolean(hasFault),
    }
  })

  const rows: TraceRow[] = []
  const offset = (span: SpanRecord) => epochMs(span.start_time) - runStart
  const spanRow = (
    span: SpanRecord,
    level: 1 | 2 | 3,
    parentId: string | null,
    groupId: string,
    label: string,
  ): TraceRow => ({
    id: span.span_id,
    level,
    parentId,
    groupId,
    kind: span.kind,
    span,
    label,
    narrative: null,
    outcome: spanOutcome(span, trace, childrenOf(span)),
    startMs: offset(span),
    durationMs: span.duration_ms,
    childIds: [],
    fault: attrString(span, ATTR.fault) ?? attrString(span, ATTR.modelFault),
  })

  if (root) {
    const runRow = spanRow(root, 1, null, root.span_id, 'Run')
    runRow.narrative = `${trace.agent}@${trace.agent_version} · ${plural(trace.steps, 'step')} · ${trace.status.replace('_', ' ')}`
    rows.push(runRow)
  }
  groups.forEach((group, i) => {
    const summary = stepGroups[i]!
    const nodeRows: TraceRow[] = []
    const groupRow: TraceRow = {
      id: group.id,
      level: 1,
      parentId: null,
      groupId: group.id,
      kind: group.kind,
      span: null,
      label: summary.label,
      narrative: summary.narrative,
      outcome: summary.outcome,
      startMs: totalMs,
      durationMs: 0,
      childIds: [],
      fault: null,
    }
    rows.push(groupRow)
    for (const node of group.nodes) {
      const nodeRow = spanRow(node, 2, group.id, group.id, node.kind === 'node' ? nodeName(node) : node.name)
      groupRow.childIds.push(nodeRow.id)
      nodeRows.push(nodeRow)
      rows.push(nodeRow)
      const leaves = childrenOf(node)
      const attempts = leaves.filter((c) => c.kind === 'tool').length
      for (const leaf of leaves) {
        const attempt = attrNumber(leaf, ATTR.attempt)
        const label = attempts > 1 && attempt ? `${leaf.name} · attempt ${attempt}` : leaf.name
        const leafRow = spanRow(leaf, 3, node.span_id, group.id, label)
        nodeRow.childIds.push(leafRow.id)
        rows.push(leafRow)
      }
    }
    if (nodeRows.length > 0) {
      const start = Math.min(...nodeRows.map((r) => r.startMs))
      const end = Math.max(...nodeRows.map((r) => r.startMs + r.durationMs))
      groupRow.startMs = start
      groupRow.durationMs = end - start
    }
  })

  const rowById = new Map(rows.map((row) => [row.id, row]))
  const scaled = rows.filter((row) => row.kind !== 'run').map((row) => row.durationMs)
  return {
    rows,
    rowById,
    groups: stepGroups,
    runRowId: root?.span_id ?? null,
    totalMs,
    relativeScaleMs: Math.max(0.001, ...scaled),
    toolTimeoutMs: trace.limits.tool_timeout_s * 1000,
    simulated: trace.provider === SIMULATED_PROVIDER,
  }
}

/** Rows whose ancestors are all expanded, in display order. */
export function visibleRows(model: TraceModel, expanded: ReadonlySet<string>): TraceRow[] {
  const shown = new Set<string>()
  const out: TraceRow[] = []
  for (const row of model.rows) {
    if (row.parentId === null || (shown.has(row.parentId) && expanded.has(row.parentId))) {
      shown.add(row.id)
      out.push(row)
    }
  }
  return out
}

/** Ids of every ancestor of a row, nearest first. */
export function ancestorIds(model: TraceModel, id: string): string[] {
  const out: string[] = []
  let parent = model.rowById.get(id)?.parentId ?? null
  while (parent) {
    out.push(parent)
    parent = model.rowById.get(parent)?.parentId ?? null
  }
  return out
}
