<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'
import { RouterLink } from 'vue-router'
import type { RunDetail } from '@/api/types'
import Icon from '@/components/ui/Icon.vue'
import JsonView from '@/components/ui/JsonView.vue'
import Kbd from '@/components/ui/Kbd.vue'
import StatusBadge from '@/components/ui/StatusBadge.vue'
import { canonicalJson, shortId } from '@/composables/format'
import { SIMULATED_PROVIDER, type Outcome } from '@/composables/useTraceModel'
import { alignSteps, differingFacets, divergences, traceSteps, type AlignedRow, type RowKind } from './alignSteps'

/*
 * Two runs side by side, aligned step by step (LCS over decision type + tool). Unmatched
 * steps are gap rows, a callout names the first divergence, and n / Shift N (while the diff
 * has focus) or the buttons jump between divergences. Equality ignores ids, timestamps and
 * durations.
 */
const props = withDefaults(defineProps<{ a: RunDetail; b: RunDetail; labelA?: string; labelB?: string }>(), {
  labelA: 'A',
  labelB: 'B',
})

const rows = computed(() => alignSteps(traceSteps(props.a.trace), traceSteps(props.b.trace)))
const diverging = computed(() => divergences(rows.value))
const current = ref(0)
const table = ref<HTMLElement | null>(null)
watch(rows, () => (current.value = 0))

const simulated = computed(
  () => props.a.trace.provider === SIMULATED_PROVIDER && props.b.trace.provider === SIMULATED_PROVIDER,
)
/** Same deterministic model, same effective configuration, same input: a replay. */
const replay = computed(() => {
  const [a, b] = [props.a.trace, props.b.trace]
  return (
    simulated.value &&
    a.effective_config_hash === b.effective_config_hash &&
    a.task === b.task &&
    canonicalJson([a.faults, a.model_faults]) === canonicalJson([b.faults, b.model_faults])
  )
})
const finalsDiffer = computed(
  () =>
    canonicalJson(props.a.trace.final_output) !== canonicalJson(props.b.trace.final_output) ||
    props.a.trace.status !== props.b.trace.status,
)

const COLUMNS = ['a', 'kind', 'b'] as const
// The column headers name A and B in full; the badges stay short enough for the column.
const KIND: Record<RowKind, Outcome> = {
  same: { tone: 'ok', label: 'same' },
  changed: { tone: 'warn', label: 'changed' },
  only_a: { tone: 'fail', label: 'only in A' },
  only_b: { tone: 'fail', label: 'only in B' },
}

function describe(row: AlignedRow): string {
  if (row.kind === 'only_a' && row.a) {
    return `${props.labelA} step ${row.a.index} (${row.a.signature}) has no counterpart in ${props.labelB}: ${row.a.narrative}`
  }
  if (row.kind === 'only_b' && row.b) {
    return `${props.labelB} step ${row.b.index} (${row.b.signature}) has no counterpart in ${props.labelA}: ${row.b.narrative}`
  }
  if (row.a && row.b) {
    const facets = differingFacets(row.a, row.b)
    return `${props.labelA} step ${row.a.index} and ${props.labelB} step ${row.b.index} both made a ${row.a.signature} decision, but ${facets.join(', ')} ${facets.length === 1 ? 'differs' : 'differ'}`
  }
  return ''
}
const first = computed(() => {
  const index = diverging.value[0]
  return index === undefined ? null : rows.value[index]!
})

async function jump(delta: number): Promise<void> {
  const count = diverging.value.length
  if (!count) return
  current.value = (current.value + delta + count) % count
  await nextTick()
  table.value?.querySelector(`[data-row="${diverging.value[current.value]}"]`)?.scrollIntoView?.({ block: 'nearest' })
}

function onKeydown(event: KeyboardEvent): void {
  if (event.ctrlKey || event.metaKey || event.altKey) return
  if (event.key === 'n') void jump(1)
  else if (event.key === 'N') void jump(-1)
  else return
  event.preventDefault()
}

const stepLink = (detail: RunDetail, rowId: string) => ({
  path: `/runs/${encodeURIComponent(detail.trace.run_id)}`,
  query: { span: rowId },
})
const failedChecks = (detail: RunDetail) =>
  detail.evaluation?.checks.filter((c) => c.status === 'failed').map((c) => c.name) ?? []
</script>

<template>
  <section class="trace-diff" aria-label="Trace diff">
    <p v-if="replay && !diverging.length && !finalsDiffer" class="callout ok banner" role="status">
      <Icon name="check" :size="14" />
      <span><strong>Identical: deterministic replay.</strong> Both runs used the simulated model with the same effective configuration and input, and made the same decisions.</span>
    </p>
    <p v-else-if="first" class="callout warn" role="status">
      <Icon name="alert" :size="14" />
      <span><strong>First divergence:</strong> {{ describe(first) }}.</span>
    </p>
    <p v-else class="callout info" role="status">
      <Icon name="check" :size="14" />
      <span>
        Every step matches.<template v-if="finalsDiffer"> The final outputs or statuses still differ: see below.</template>
      </span>
    </p>

    <div class="toolbar">
      <span class="muted">{{ diverging.length }} divergence{{ diverging.length === 1 ? '' : 's' }} in {{ rows.length }} aligned steps</span>
      <button type="button" class="btn" :disabled="!diverging.length" @click="jump(-1)">Previous <Kbd>Shift N</Kbd></button>
      <button type="button" class="btn" :disabled="!diverging.length" @click="jump(1)">Next <Kbd>n</Kbd></button>
    </div>

    <div ref="table" class="table-wrap panel steps-wrap" role="region" aria-label="Aligned steps" tabindex="0" @keydown="onKeydown">
      <table class="data-table steps">
        <thead>
          <tr>
            <th scope="col">{{ labelA }} · run {{ shortId(a.trace.run_id) }}</th>
            <th scope="col" class="kind-col">Alignment</th>
            <th scope="col">{{ labelB }} · run {{ shortId(b.trace.run_id) }}</th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="(row, i) in rows"
            :key="i"
            :data-row="i"
            :class="[row.kind, { current: diverging[current] === i }]"
            :aria-current="diverging[current] === i ? 'true' : undefined"
          >
            <template v-for="col in COLUMNS" :key="col">
              <td v-if="col === 'kind'" class="kind-col">
                <StatusBadge :outcome="KIND[row.kind]" />
                <span v-if="row.kind === 'changed'" class="facets">{{ differingFacets(row.a!, row.b!).join(', ') }}</span>
              </td>
              <td v-else :class="['step', col, { gap: !row[col] }]">
                <template v-if="row[col]">
                  <RouterLink :to="stepLink(col === 'a' ? a : b, row[col]!.rowId)" class="step-link">
                    <span class="mono index">S{{ row[col]!.index }}</span>
                    <span class="tag">{{ row[col]!.signature }}</span>
                    <StatusBadge :outcome="row[col]!.outcome" variant="plain" />
                  </RouterLink>
                  <span class="narrative">{{ row[col]!.narrative }}</span>
                </template>
                <span v-else class="gap-label">no matching step</span>
              </td>
            </template>
          </tr>
        </tbody>
      </table>
    </div>

    <div class="finals">
      <section v-for="side in (['a', 'b'] as const)" :key="side" class="final panel" :aria-label="`Final result of ${side === 'a' ? labelA : labelB}`">
        <h3 class="section-title">{{ side === 'a' ? labelA : labelB }} · final</h3>
        <p class="line">
          <StatusBadge :status="(side === 'a' ? a : b).trace.status" />
          <template v-if="(side === 'a' ? a : b).evaluation">
            <StatusBadge :status="(side === 'a' ? a : b).evaluation!.passed ? 'passed' : 'failed'" variant="plain" />
            <span v-if="failedChecks(side === 'a' ? a : b).length" class="muted mono failed">
              {{ failedChecks(side === 'a' ? a : b).join(', ') }}
            </span>
          </template>
          <span v-else class="faint">not evaluated</span>
        </p>
        <JsonView :value="(side === 'a' ? a : b).trace.final_output" :label="`Final output of ${side === 'a' ? labelA : labelB}`" />
      </section>
    </div>
  </section>
</template>

<style scoped>
.trace-diff {
  display: grid;
  gap: 10px;
}
.banner strong {
  margin-right: 4px;
}
.toolbar {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  font-size: 12.5px;
}
.toolbar .muted {
  margin-right: auto;
}
.steps-wrap:focus-visible {
  outline-offset: 2px;
}
.steps {
  table-layout: fixed;
}
.kind-col {
  width: 150px;
  text-align: center;
}
.facets {
  display: block;
  margin-top: 2px;
  font-size: 11px;
  color: var(--text-muted);
}
.step {
  vertical-align: top;
}
.step-link {
  display: inline-flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
  color: var(--text);
}
.step-link:hover .index {
  color: var(--accent);
}
.narrative {
  display: block;
  margin-top: 2px;
  font-size: 12px;
  color: var(--text-muted);
  overflow-wrap: anywhere;
}
.gap {
  background: repeating-linear-gradient(-45deg, var(--surface-2) 0 4px, transparent 4px 8px);
}
.gap-label {
  display: inline-block;
  padding: 2px 8px;
  border: 1px dashed var(--border-strong);
  border-radius: var(--radius-sm);
  background: var(--surface-1);
  font-size: 12px;
  color: var(--text-muted);
}
tr.changed > td,
tr.only_a > td,
tr.only_b > td {
  box-shadow: inset 0 1px 0 var(--border);
}
tr.current > td {
  background-color: var(--selected);
}
tr.current > td.gap {
  background: var(--selected);
}
.finals {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
}
.final {
  display: grid;
  gap: 8px;
  align-content: start;
  padding: 10px 12px;
}
.line {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  margin: 0;
}
.failed {
  font-size: 11.5px;
}
</style>
