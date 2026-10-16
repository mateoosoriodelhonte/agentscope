<script lang="ts">
import type { CheckName, EvalCase, TraceRecord } from '@/api/types'

export type SequenceOp =
  | { kind: 'match'; tool: string }
  | { kind: 'missing'; tool: string }
  | { kind: 'extra'; tool: string }

/** Only `tool_order` judges the order of calls; the other checks count them. */
export function judgesOrder(check: CheckName): boolean {
  return check === 'tool_order'
}

/**
 * The tools a check expects. For `tool_order` that is the declared order. Otherwise it is
 * each expected call × min_calls; those checks count calls and ignore their order, so the
 * expected tools are laid out in the order they actually executed (with the shortfall at the
 * end). Aligning against the declaration order instead would report calls that did happen
 * as missing.
 */
export function expectedTools(evalCase: EvalCase, check: CheckName, executed: string[]): string[] {
  if (judgesOrder(check)) return [...evalCase.tool_order]
  const remaining = new Map<string, number>()
  for (const call of evalCase.expected_tool_calls) {
    remaining.set(call.tool, (remaining.get(call.tool) ?? 0) + call.min_calls)
  }
  const ordered: string[] = []
  for (const tool of executed) {
    const left = remaining.get(tool) ?? 0
    if (left > 0) {
      ordered.push(tool)
      remaining.set(tool, left - 1)
    }
  }
  for (const [tool, left] of remaining) ordered.push(...Array<string>(left).fill(tool))
  return ordered
}

export function executedTools(trace: TraceRecord): string[] {
  return trace.tool_invocations.filter((i) => i.executed).map((i) => i.tool)
}

/** Aligns expected and executed tool sequences with a longest common subsequence. */
export function diffToolSequence(expected: string[], actual: string[]): SequenceOp[] {
  const n = expected.length
  const m = actual.length
  const lcs = Array.from({ length: n + 1 }, () => Array<number>(m + 1).fill(0))
  for (let i = n - 1; i >= 0; i--) {
    for (let j = m - 1; j >= 0; j--) {
      lcs[i]![j] =
        expected[i] === actual[j] ? lcs[i + 1]![j + 1]! + 1 : Math.max(lcs[i + 1]![j]!, lcs[i]![j + 1]!)
    }
  }
  const ops: SequenceOp[] = []
  let i = 0
  let j = 0
  while (i < n || j < m) {
    if (i < n && j < m && expected[i] === actual[j]) {
      ops.push({ kind: 'match', tool: expected[i]! })
      i++
      j++
    } else if (j < m && (i === n || lcs[i]![j + 1]! >= lcs[i + 1]![j]!)) {
      ops.push({ kind: 'extra', tool: actual[j]! })
      j++
    } else {
      ops.push({ kind: 'missing', tool: expected[i]! })
      i++
    }
  }
  return ops
}
</script>

<script setup lang="ts">
import { computed } from 'vue'

/*
 * Expected vs executed tools for failures caused by something missing: missing calls are
 * dashed ghost chips in the executed row. When the check judges order (`ordered`), calls
 * outside the expected sequence are marked as extra; otherwise they are shown plainly,
 * because executing other tools is not what such a check penalises.
 */
const props = withDefaults(
  defineProps<{ expected: string[]; actual: string[]; blocked?: number; ordered?: boolean }>(),
  { blocked: 0, ordered: true },
)
const ops = computed(() => diffToolSequence(props.expected, props.actual))
const missing = computed(() => ops.value.filter((op) => op.kind === 'missing').length)
</script>

<template>
  <figure class="diff">
    <figcaption class="visually-hidden">
      Expected tool sequence compared with the executed sequence; {{ missing }} expected
      {{ missing === 1 ? 'call is' : 'calls are' }} missing.
    </figcaption>
    <div class="grid" role="table" aria-label="Expected and executed tools">
      <div class="row" role="row">
        <span class="head" role="rowheader">Expected</span>
        <span v-for="(op, i) in ops" :key="i" class="cell" role="cell">
          <span v-if="op.kind !== 'extra'" class="chip" :title="op.tool">{{ op.tool }}</span>
        </span>
      </div>
      <div class="row" role="row">
        <span class="head" role="rowheader">Executed</span>
        <span v-for="(op, i) in ops" :key="i" class="cell" role="cell">
          <span v-if="op.kind === 'missing'" class="chip ghost" :title="`${op.tool} was expected here but never executed`">
            <span class="visually-hidden">missing:</span>{{ op.tool }}
          </span>
          <span v-else class="chip" :class="{ extra: ordered && op.kind === 'extra' }" :title="op.tool">
            <span v-if="ordered && op.kind === 'extra'" class="visually-hidden">extra:</span>{{ op.tool }}
          </span>
        </span>
      </div>
    </div>
    <p v-if="blocked" class="note">
      {{ blocked }} blocked {{ blocked === 1 ? 'call is' : 'calls are' }} not shown: blocked calls never
      execute.
    </p>
  </figure>
</template>

<style scoped>
.diff {
  margin: 8px 0 0;
}
.grid {
  display: grid;
  gap: 6px;
  overflow-x: auto;
}
.row {
  display: flex;
  align-items: center;
  gap: 6px;
}
.head {
  flex: none;
  width: 64px;
  font-size: 11px;
  color: var(--text-muted);
}
.cell {
  display: inline-flex;
  flex: 1 1 0;
  min-width: 0;
  max-width: 160px;
}
.chip {
  overflow: hidden;
  max-width: 100%;
  padding: 1px 6px;
  border: 1px solid var(--border-strong);
  border-radius: var(--radius-sm);
  background: var(--surface-1);
  font-family: var(--font-mono);
  font-size: 11px;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.chip.ghost {
  border-style: dashed;
  border-color: var(--fail);
  background: transparent;
  color: var(--fail);
}
.chip.extra {
  border-color: var(--warn);
  color: var(--warn);
}
.note {
  margin: 6px 0 0;
  font-size: 11.5px;
  color: var(--text-muted);
}
</style>
