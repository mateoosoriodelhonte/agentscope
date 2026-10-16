import type { JsonValue, SpanRecord, TraceRecord } from '@/api/types'
import { canonicalJson } from '@/composables/format'
import {
  ATTR,
  attrString,
  decisionOf,
  nodeName,
  useTraceModel,
  type Outcome,
} from '@/composables/useTraceModel'

/** Anything alignable: steps match on `signature`, and matched steps differ on `content`. */
export interface Alignable {
  signature: string
  content: string
}

/** One step of a run, reduced to what two runs can be compared on. */
export interface DiffStep extends Alignable {
  /** 1-based step number in its own run. */
  index: number
  /** tool_call, final, invalid (no valid decision) or error (the model call failed). */
  decision: string
  tool: string | null
  /** Canonical JSON of each part of the step; `content` is all of them together. */
  facets: Record<Facet, string>
  narrative: string
  outcome: Outcome
  /** Row id of the step in the trace explorer (`step-N`), for deep links. */
  rowId: string
}

export type RowKind = 'same' | 'changed' | 'only_a' | 'only_b'

export interface AlignedRow<T extends Alignable = DiffStep> {
  kind: RowKind
  a: T | null
  b: T | null
}

/** A step's signature: the decision type plus the tool it asked for. */
export function stepSignature(decision: string, tool: string | null): string {
  return tool ? `${decision}:${tool}` : decision
}

/**
 * Aligns two step sequences by their longest common subsequence of signatures. Matched
 * pairs are `same` or `changed` (same signature, different content); unmatched steps are gap
 * rows (`only_a` / `only_b`). Where both runs have unmatched steps between two matches, the
 * A-side steps come first. Pure, O(n·m); fine for runs of up to 50 steps.
 */
export function alignSteps<T extends Alignable>(a: readonly T[], b: readonly T[]): AlignedRow<T>[] {
  const n = a.length
  const m = b.length
  // lcs[i][j] = length of the LCS of a[i..] and b[j..]
  const lcs = Array.from({ length: n + 1 }, () => new Array<number>(m + 1).fill(0))
  for (let i = n - 1; i >= 0; i--) {
    for (let j = m - 1; j >= 0; j--) {
      lcs[i]![j] =
        a[i]!.signature === b[j]!.signature ? lcs[i + 1]![j + 1]! + 1 : Math.max(lcs[i + 1]![j]!, lcs[i]![j + 1]!)
    }
  }
  const rows: AlignedRow<T>[] = []
  let i = 0
  let j = 0
  while (i < n || j < m) {
    if (i < n && j < m && a[i]!.signature === b[j]!.signature) {
      rows.push({ kind: a[i]!.content === b[j]!.content ? 'same' : 'changed', a: a[i]!, b: b[j]! })
      i++
      j++
    } else if (i < n && (j === m || lcs[i + 1]![j]! >= lcs[i]![j + 1]!)) {
      rows.push({ kind: 'only_a', a: a[i]!, b: null })
      i++
    } else {
      rows.push({ kind: 'only_b', a: null, b: b[j]! })
      j++
    }
  }
  return rows
}

/** Indices of the rows where the runs diverge, in order. */
export function divergences(rows: readonly AlignedRow<Alignable>[]): number[] {
  return rows.flatMap((row, index) => (row.kind === 'same' ? [] : [index]))
}

type JsonObject = { [key: string]: JsonValue }

/** The parts of a step that two runs can differ in, in the order a reader would check them. */
export const FACETS = {
  decision: 'the decision',
  execution: 'whether the call was permitted and ran',
  attempts: 'the call attempts',
  outcome: 'the tool outcome',
  output: 'the tool result',
  model_fault: 'the injected model fault',
  error: 'the model error',
} as const
export type Facet = keyof typeof FACETS

/**
 * What a step did, without ids, timestamps or durations: the decision (minus its free-text
 * rationale, which varies between model runs without changing behaviour), what happened to
 * the tool call, and any injected model fault. Each facet is canonical JSON.
 */
function stepFacets(trace: TraceRecord, index: number, chat: SpanRecord | undefined): Record<Facet, string> {
  const decision = decisionOf(chat)
  let parsed: JsonValue = decision?.raw ?? null
  if (decision?.value) {
    const rest: JsonObject = { ...decision.value }
    delete rest.rationale
    parsed = rest
  }
  const invocation = trace.tool_invocations.find((inv) => inv.step === index)
  return {
    decision: canonicalJson(parsed),
    execution: canonicalJson(invocation ? [invocation.permitted, invocation.executed] : null),
    attempts: canonicalJson(invocation?.attempts.map((a) => [a.outcome, a.fault, a.error]) ?? null),
    outcome: canonicalJson(invocation?.outcome ?? null),
    output: canonicalJson(invocation?.output ?? null),
    model_fault: canonicalJson(attrString(chat, ATTR.modelFault)),
    error: canonicalJson(chat?.status === 'error' ? chat.status_message : null),
  }
}

/** What differs between two matched steps, as phrases ("the tool result"). */
export function differingFacets(a: DiffStep, b: DiffStep): string[] {
  return (Object.keys(FACETS) as Facet[]).filter((f) => a.facets[f] !== b.facets[f]).map((f) => FACETS[f])
}

/** The steps of a run in order, built on the trace explorer's step model. */
export function traceSteps(trace: TraceRecord): DiffStep[] {
  const model = useTraceModel(trace)
  return model.groups
    .filter((group) => group.kind === 'step')
    .map((group) => {
      const plan = model.rows.find(
        (row) => row.groupId === group.id && row.level === 2 && row.span && nodeName(row.span) === 'plan',
      )?.span
      const chat = model.rows.find((row) => row.parentId === plan?.span_id && row.kind === 'llm')?.span ?? undefined
      const recorded = attrString(plan, ATTR.decision)
      const decision = recorded ?? (chat?.status === 'error' ? 'error' : 'invalid')
      const tool = recorded === 'tool_call' ? attrString(plan, ATTR.toolName) : null
      const facets = stepFacets(trace, group.index, chat)
      return {
        index: group.index,
        decision,
        tool,
        signature: stepSignature(decision, tool),
        facets,
        content: canonicalJson(facets),
        narrative: group.narrative,
        outcome: group.outcome,
        rowId: group.id,
      }
    })
}
