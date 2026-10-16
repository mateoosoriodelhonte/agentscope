<script setup lang="ts">
import { computed } from 'vue'
import { RouterLink } from 'vue-router'
import type { PassRateSeries } from '@/api/types'
import StatusBadge from '@/components/ui/StatusBadge.vue'
import { gateOutcome } from '@/components/evaluation/gate'
import { formatPercent, formatTimestamp } from '@/composables/format'
import SeriesMarker from './SeriesMarker.vue'

/*
 * Pass rate per agent version over the sequence of evaluations, one line per agent_ref, with
 * the gate's minimum pass rate as a dashed rule. Colour follows the agent (by its sorted
 * position among all series, so filtering never repaints a line), each line also has its
 * own marker shape, and the table view holds every value.
 */
const props = defineProps<{
  series: PassRateSeries[]
  /** Show only this agent_ref; null shows all. */
  only: string | null
  /** The gate's minimum pass rate (0–1), when a gate exists. */
  threshold: number | null
  view: 'chart' | 'table'
}>()

const MAX_LINES = 4
const W = 640
const H = 220
const M = { top: 12, right: 18, bottom: 28, left: 44 }
const plotW = W - M.left - M.right
const plotH = H - M.top - M.bottom

const indexOf = computed(() => {
  const refs = [...new Set(props.series.map((s) => s.agent_ref))].sort()
  return new Map(refs.map((ref, i) => [ref, i]))
})
const shown = computed(() =>
  props.series
    .filter((s) => s.points.length && (!props.only || s.agent_ref === props.only))
    .sort((a, b) => indexOf.value.get(a.agent_ref)! - indexOf.value.get(b.agent_ref)!),
)
const charted = computed(() => shown.value.slice(0, MAX_LINES))

/** One x position per evaluation, in the order they finished. */
const sequence = computed(() => {
  const points = shown.value.flatMap((s) => s.points)
  points.sort((a, b) => a.finished_at.localeCompare(b.finished_at) || a.suite_id.localeCompare(b.suite_id))
  return new Map([...new Set(points.map((p) => p.suite_id))].map((id, i) => [id, i]))
})
const count = computed(() => sequence.value.size)

function x(index: number): number {
  return count.value <= 1 ? M.left + plotW / 2 : M.left + (index * plotW) / (count.value - 1)
}
function y(rate: number): number {
  return M.top + (1 - rate) * plotH
}

const lines = computed(() =>
  charted.value.map((s) => {
    const points = s.points
      .map((p) => ({ ...p, index: sequence.value.get(p.suite_id)! }))
      .sort((a, b) => a.index - b.index)
    return {
      ref: s.agent_ref,
      series: indexOf.value.get(s.agent_ref)! % MAX_LINES,
      path: points.map((p) => `${x(p.index).toFixed(1)},${y(p.pass_rate).toFixed(1)}`).join(' '),
      points,
    }
  }),
)

const yTicks = [0, 0.25, 0.5, 0.75, 1]
const xTicks = computed(() => {
  const step = Math.max(1, Math.ceil(count.value / 10))
  return Array.from({ length: count.value }, (_, i) => i).filter((i) => i % step === 0 || i === count.value - 1)
})

const summary = computed(() => {
  const latest = charted.value.map((s) => `${s.agent_ref} ${formatPercent(s.points[s.points.length - 1]!.pass_rate)}`)
  const rule = props.threshold === null ? '' : `; gate minimum ${formatPercent(props.threshold)}`
  return `Pass rate over ${count.value} evaluations. Latest: ${latest.join(', ')}${rule}. The table view lists every value.`
})

const rows = computed(() =>
  shown.value
    .flatMap((s) => s.points.map((p) => ({ ref: s.agent_ref, ...p, index: sequence.value.get(p.suite_id)! })))
    .sort((a, b) => a.index - b.index || a.ref.localeCompare(b.ref)),
)
</script>

<template>
  <div class="pass-rate">
    <template v-if="view === 'chart'">
      <ul class="legend" aria-label="Legend">
        <template v-if="charted.length > 1">
          <li v-for="line in lines" :key="line.ref">
            <svg width="14" height="14" viewBox="-7 -7 14 14" aria-hidden="true">
              <SeriesMarker :series="line.series" :x="0" :y="0" />
            </svg>
            <span class="mono">{{ line.ref }}</span>
          </li>
        </template>
        <li v-else-if="charted.length === 1" class="mono">{{ charted[0]!.agent_ref }}</li>
        <li v-if="threshold !== null" class="rule">
          <svg width="20" height="8" aria-hidden="true"><line x1="0" x2="20" y1="4" y2="4" /></svg>
          gate minimum {{ formatPercent(threshold) }}
        </li>
      </ul>
      <svg class="plot" :viewBox="`0 0 ${W} ${H}`" role="img" :aria-label="summary">
        <g class="grid">
          <g v-for="t in yTicks" :key="t">
            <line :x1="M.left" :x2="W - M.right" :y1="y(t)" :y2="y(t)" />
            <text :x="M.left - 8" :y="y(t)" dy="0.32em" text-anchor="end">{{ formatPercent(t) }}</text>
          </g>
          <text v-for="i in xTicks" :key="i" :x="x(i)" :y="H - 8" text-anchor="middle">#{{ i + 1 }}</text>
        </g>
        <line v-if="threshold !== null" class="threshold" :x1="M.left" :x2="W - M.right" :y1="y(threshold)" :y2="y(threshold)" />
        <g v-for="line in lines" :key="line.ref" :class="`series s${line.series + 1}`">
          <polyline v-if="line.points.length > 1" :points="line.path" />
          <SeriesMarker v-for="p in line.points" :key="p.suite_id" :series="line.series" :x="x(p.index)" :y="y(p.pass_rate)">
            <title>
              {{ line.ref }} · evaluation #{{ p.index + 1 }} · {{ formatPercent(p.pass_rate) }} · {{ p.dataset }} ·
              {{ gateOutcome(p.gate_passed).label }} · {{ formatTimestamp(p.finished_at) }}
            </title>
          </SeriesMarker>
        </g>
      </svg>
      <p v-if="shown.length > MAX_LINES" class="faint more">
        The chart shows {{ MAX_LINES }} of {{ shown.length }} agent versions; the table lists all of them.
      </p>
    </template>
    <div v-else class="table-wrap panel">
      <table class="data-table">
        <caption class="visually-hidden">Pass rate per agent version for each evaluation</caption>
        <thead>
          <tr>
            <th scope="col" class="num">#</th>
            <th scope="col">Agent</th>
            <th scope="col">Dataset</th>
            <th scope="col" class="num">Pass rate</th>
            <th scope="col">Gate</th>
            <th scope="col">Finished</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in rows" :key="row.suite_id + row.ref">
            <td class="num mono">{{ row.index + 1 }}</td>
            <td class="mono">{{ row.ref }}</td>
            <td class="mono">{{ row.dataset }}</td>
            <td class="num mono">
              <RouterLink :to="`/evaluations/${encodeURIComponent(row.suite_id)}`">{{ formatPercent(row.pass_rate) }}</RouterLink>
            </td>
            <td><StatusBadge :outcome="gateOutcome(row.gate_passed)" variant="plain" /></td>
            <td class="faint">{{ formatTimestamp(row.finished_at) }}</td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>

<style scoped>
.pass-rate {
  display: grid;
  gap: 6px;
}
.legend {
  display: flex;
  flex-wrap: wrap;
  gap: 4px 16px;
  margin: 0;
  padding: 0;
  list-style: none;
  font-size: 12px;
  color: var(--text-muted);
}
.legend li {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}
.plot {
  width: 100%;
  height: auto;
  max-height: 280px;
  overflow: visible;
}
.grid line {
  stroke: var(--border);
  stroke-width: 1;
}
.grid text {
  fill: var(--text-muted);
  font-size: 11px;
  font-family: var(--font-mono);
}
.threshold,
.rule line {
  stroke: var(--text-muted);
  stroke-width: 1.5;
  stroke-dasharray: 5 4;
}
polyline {
  fill: none;
  stroke-width: 2;
  stroke-linejoin: round;
  stroke-linecap: round;
}
.s1 polyline {
  stroke: var(--series-1);
}
.s2 polyline {
  stroke: var(--series-2);
}
.s3 polyline {
  stroke: var(--series-3);
}
.s4 polyline {
  stroke: var(--series-4);
}
.more {
  margin: 0;
  font-size: 12px;
}
</style>
