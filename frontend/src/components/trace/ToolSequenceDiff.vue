<script lang="ts">
import type { EvalCase, TraceRecord } from '@/api/types'

export type SequenceOp =
  | { kind: 'match'; tool: string }
  | { kind: 'missing'; tool: string }
  | { kind: 'extra'; tool: string }

/** The tools a case expects, in order: `tool_order`, else each expected call × min_calls. */
export function expectedTools(evalCase: EvalCase): string[] {
  if (evalCase.tool_order.length) return [...evalCase.tool_order]
  return evalCase.expected_tool_calls.flatMap((call) => Array<string>(call.min_calls).fill(call.tool))
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
 * dashed ghost chips in the executed row, unexpected extra calls are marked in the same row.
 */
const props = defineProps<{ expected: string[]; actual: string[]; blocked?: number }>()
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
          <span v-else class="chip" :class="{ extra: op.kind === 'extra' }" :title="op.tool">
            <span v-if="op.kind === 'extra'" class="visually-hidden">extra:</span>{{ op.tool }}
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
