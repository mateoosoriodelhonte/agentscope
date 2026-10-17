<script setup lang="ts">
import { computed } from 'vue'
import { RouterLink } from 'vue-router'
import type { PassRatePoint, PassRateSeries } from '@/api/types'
import StatusBadge from '@/components/ui/StatusBadge.vue'
import { gateOutcome } from '@/components/evaluation/gate'
import { formatPercent, formatTimestamp } from '@/composables/format'
import SeriesMarker from './SeriesMarker.vue'

/*
 * Pass rate over the sequence of evaluations, one line per agent version and dataset version:
 * pass rates on different datasets are not comparable, so joining them would draw a dataset
 * switch as a regression. A gate's minimum pass rate is a dashed rule drawn only while the
 * line of the suite it was applied to is shown: other agents were never held to it. Colour
 * follows the line (by its sorted position among all lines, so filtering never repaints
 * one), each line also has its own marker shape, and the table view holds every value.
 */
const props = defineProps<{
  series: PassRateSeries[]
  /** Show only this agent_ref; null shows all. */
  only: string | null
  /** A gate's minimum pass rate (0–1) and the suite it was applied to, when one exists. */
  threshold: { value: number; suiteId: string } | null
  view: 'chart' | 'table'
}>()

interface Line {
  key: string
  agentRef: string
  dataset: string
  points: PassRatePoint[]
}

const MAX_LINES = 4
const W = 640
const H = 220
const M = { top: 12, right: 18, bottom: 28, left: 44 }
const plotW = W - M.left - M.right
const plotH = H - M.top - M.bottom

/** The API's per-agent series, split by dataset version. */
const allLines = computed<Line[]>(() => {
  const byKey = new Map<string, Line>()
  for (const s of props.series) {
    for (const point of s.points) {
      const key = `${s.agent_ref} · ${point.dataset}`
      const line = byKey.get(key) ?? { key, agentRef: s.agent_ref, dataset: point.dataset, points: [] }
      line.points.push(point)
      byKey.set(key, line)
    }
  }
  return [...byKey.values()].sort((a, b) => (a.key < b.key ? -1 : a.key > b.key ? 1 : 0))
})
/** With one dataset everywhere, the agent version alone names a line. */
const oneDataset = computed(() => new Set(allLines.value.map((l) => l.dataset)).size <= 1)
const labelOf = (line: Line) => (oneDataset.value ? line.agentRef : line.key)
const indexOf = computed(() => new Map(allLines.value.map((line, i) => [line.key, i])))
const shown = computed(() => allLines.value.filter((l) => !props.only || l.agentRef === props.only))
const charted = computed(() => shown.value.slice(0, MAX_LINES))
/** The line the gate was applied to, while it is charted; otherwise no rule is drawn. */
const gated = computed(() => {
  const t = props.threshold
  const line = t ? charted.value.find((l) => l.points.some((p) => p.suite_id === t.suiteId)) : undefined
  return t && line ? { value: t.value, label: labelOf(line) } : null
})
/** The gated line when it is listed but past the charted ones: said in words instead. */
const gatedHidden = computed(() => {
  const t = props.threshold
  if (!t || gated.value) return null
  const line = shown.value.find((l) => l.points.some((p) => p.suite_id === t.suiteId))
  return line ? { value: t.value, label: labelOf(line) } : null
})

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
  charted.value.map((line) => {
    const points = line.points
      .map((p) => ({ ...p, index: sequence.value.get(p.suite_id)! }))
      .sort((a, b) => a.index - b.index)
    return {
      key: line.key,
      label: labelOf(line),
      series: indexOf.value.get(line.key)! % MAX_LINES,
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
  const latest = lines.value.map((l) => `${l.label} ${formatPercent(l.points[l.points.length - 1]!.pass_rate)}`)
  const rule = gated.value ? `; gate minimum ${formatPercent(gated.value.value)} for ${gated.value.label}` : ''
  return `Pass rate over ${count.value} evaluations. Latest: ${latest.join(', ')}${rule}. The table view lists every value.`
})

const rows = computed(() =>
  shown.value
    .flatMap((l) => l.points.map((p) => ({ ref: l.agentRef, ...p, index: sequence.value.get(p.suite_id)! })))
    .sort((a, b) => a.index - b.index || a.ref.localeCompare(b.ref)),
)
</script>

<template>
  <div class="pass-rate">
    <template v-if="view === 'chart'">
      <ul class="legend" aria-label="Legend">
        <template v-if="lines.length > 1">
          <li v-for="line in lines" :key="line.key">
            <svg width="14" height="14" viewBox="-7 -7 14 14" aria-hidden="true">
              <SeriesMarker :series="line.series" :x="0" :y="0" />
            </svg>
            <span class="mono">{{ line.label }}</span>
          </li>
        </template>
        <li v-else-if="lines.length === 1" class="mono">{{ lines[0]!.label }}</li>
        <li v-if="gated" class="rule">
          <svg width="20" height="8" aria-hidden="true"><line x1="0" x2="20" y1="4" y2="4" /></svg>
          gate minimum {{ formatPercent(gated.value) }} · <span class="mono">{{ gated.label }}</span>
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
        <line v-if="gated" class="threshold" :x1="M.left" :x2="W - M.right" :y1="y(gated.value)" :y2="y(gated.value)" />
        <g v-for="line in lines" :key="line.key" :class="`series s${line.series + 1}`">
          <polyline v-if="line.points.length > 1" :points="line.path" />
          <SeriesMarker v-for="p in line.points" :key="p.suite_id" :series="line.series" :x="x(p.index)" :y="y(p.pass_rate)">
            <title>
              {{ line.label }} · evaluation #{{ p.index + 1 }} · {{ formatPercent(p.pass_rate) }} · {{ p.dataset }} ·
              {{ gateOutcome(p.gate_passed).label }} · {{ formatTimestamp(p.finished_at) }}
            </title>
          </SeriesMarker>
        </g>
      </svg>
      <p v-if="shown.length > MAX_LINES" class="faint more">
        The chart shows {{ MAX_LINES }} of {{ shown.length }} lines; the table lists all of them.
        <template v-if="gatedHidden">
          The gate minimum of {{ formatPercent(gatedHidden.value) }} applies to {{ gatedHidden.label }}, which is not
          charted: filter to its agent version to see it.
        </template>
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
