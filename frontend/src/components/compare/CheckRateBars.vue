<script setup lang="ts">
import { computed } from 'vue'
import type { CheckFlip, MetricDelta } from '@/api/types'
import StatusBadge from '@/components/ui/StatusBadge.vue'
import { direction, formatDelta, formatSide, metricLabel } from './deltas'

/*
 * Per-check pass rate, A against B, over the cases where the check applied on both sides
 * (n). Bars carry no meaning the text does not: values, the delta in pp and n are printed
 * beside them, and the table view adds the per-check regressions and fixes.
 */
const props = defineProps<{ deltas: MetricDelta[]; flips: CheckFlip[]; view: 'chart' | 'table' }>()

const rows = computed(() =>
  props.deltas.map((d) => {
    const name = metricLabel(d)
    return { delta: d, name, flip: props.flips.find((f) => f.check === name) ?? null }
  }),
)
const width = (value: number) => `${Math.max(0, Math.min(100, value))}%`
</script>

<template>
  <div class="check-bars">
    <template v-if="view === 'chart'">
      <ul class="legend" aria-label="Legend">
        <li><span class="swatch a"></span>A</li>
        <li><span class="swatch b"></span>B</li>
      </ul>
      <ul class="rows" aria-label="Per-check pass rate, A and B">
        <li v-for="row in rows" :key="row.name" class="row">
          <span class="name mono">{{ row.name }}</span>
          <span class="bars" aria-hidden="true">
            <span class="track"><span class="bar a" :style="{ width: width(row.delta.a) }"></span></span>
            <span class="track"><span class="bar b" :style="{ width: width(row.delta.b) }"></span></span>
          </span>
          <span class="values mono">
            A {{ formatSide(row.delta, row.delta.a) }} · B {{ formatSide(row.delta, row.delta.b) }}
          </span>
          <span class="delta">
            <span class="mono">{{ formatDelta(row.delta) }}</span>
            <span class="faint mono">n={{ row.delta.n }}</span>
            <StatusBadge :outcome="direction(row.delta)" variant="plain" />
          </span>
        </li>
      </ul>
    </template>
    <div v-else class="table-wrap panel">
      <table class="data-table">
        <caption class="visually-hidden">Per-check pass rate, A and B</caption>
        <thead>
          <tr>
            <th scope="col">Check</th>
            <th scope="col" class="num">A</th>
            <th scope="col" class="num">B</th>
            <th scope="col" class="num">B − A</th>
            <th scope="col" class="num">n</th>
            <th scope="col" class="num">Regressions</th>
            <th scope="col" class="num">Fixes</th>
            <th scope="col">Change</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in rows" :key="row.name">
            <th scope="row" class="mono">{{ row.name }}</th>
            <td class="num mono">{{ formatSide(row.delta, row.delta.a) }}</td>
            <td class="num mono">{{ formatSide(row.delta, row.delta.b) }}</td>
            <td class="num mono">{{ formatDelta(row.delta) }}</td>
            <td class="num mono">{{ row.delta.n }}</td>
            <td class="num mono">{{ row.flip?.regressions ?? 0 }}</td>
            <td class="num mono">{{ row.flip?.fixes ?? 0 }}</td>
            <td><StatusBadge :outcome="direction(row.delta)" variant="plain" /></td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>

<style scoped>
.check-bars {
  display: grid;
  gap: 6px;
}
.legend,
.rows {
  margin: 0;
  padding: 0;
  list-style: none;
}
.legend {
  display: flex;
  gap: 14px;
  font-size: 12px;
  color: var(--text-muted);
}
.legend li {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}
.swatch {
  width: 14px;
  height: 8px;
  border-radius: 0 3px 3px 0;
}
.swatch.a,
.bar.a {
  background: var(--series-1);
}
.swatch.b,
.bar.b {
  background: var(--series-2);
}
.rows {
  display: grid;
  gap: 8px;
}
.row {
  display: grid;
  grid-template-columns: 170px minmax(120px, 1fr) 150px 200px;
  align-items: center;
  gap: 10px;
  font-size: 12.5px;
}
.name {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--text);
}
.bars {
  display: grid;
  gap: 2px;
}
.track {
  display: block;
  height: 8px;
  border-radius: 0 3px 3px 0;
  background: var(--surface-2);
}
.bar {
  display: block;
  height: 100%;
  min-width: 2px;
  border-radius: 0 3px 3px 0;
}
.values {
  color: var(--text-muted);
  font-size: 11.5px;
}
.delta {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
}
</style>
