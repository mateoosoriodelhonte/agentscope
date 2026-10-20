<script setup lang="ts">
import { computed, ref, useId } from 'vue'
import { RouterLink } from 'vue-router'
import type { Ablation, AblationDirection, AblationVariant, ConfigDifference, ConfigDimension } from '@/api/types'
import Icon from '@/components/ui/Icon.vue'
import { compact, formatPp, plural, ratio } from '@/composables/format'
import { dimensionName, effectDomain, formatEffect, rounded, variantName, variantOf } from './attribution'
import { DIMENSION_LABELS } from './dimensions'

/*
 * The effect of each ablated dimension on the pass rate, two ways: alone from A (A + d minus
 * A) and removed from B (B minus B − d, what B loses without it). Both are positive when the
 * dimension helps. The chart is a diverging bar per direction around zero, solid for A + d
 * and outlined for B − d, with every value, gained and lost count printed beside it; the
 * table holds the same numbers plus the flipped cases each dimension is sufficient or
 * necessary for. Expanding a dimension shows its variants, values and cases.
 */
const props = defineProps<{
  ablation: Ablation
  /** The comparison's configuration differences, for the values each variant swaps. */
  diff: ConfigDifference[]
  expanded: ConfigDimension | null
  view: 'chart' | 'table'
}>()
const emit = defineEmits<{ expand: [dimension: ConfigDimension | null] }>()

const detailId = useId()

// --- chart geometry: x in percent of the plot width, zero in the middle -----------------

const ROW_H = 36
const DIRECTIONS = ['forward', 'backward'] as const
const BAR_Y: Record<AblationDirection, number> = { forward: 6, backward: 20 }
const BAR_H = 11

const domain = computed(() =>
  effectDomain([props.ablation.total_effect_pp, ...props.ablation.variants.map((v) => v.effect_pp)]),
)
const x = (pp: number) => 50 + (Math.max(-domain.value, Math.min(domain.value, pp)) / domain.value) * 50
const ticks = computed(() => [-1, -0.5, 0, 0.5, 1].map((f) => f * domain.value))
const tickLabel = (pp: number) => (pp === 0 ? '0' : formatPp(pp))
const totalX = computed(() => x(props.ablation.total_effect_pp))

interface Bar {
  direction: AblationDirection
  x: number
  width: number
  sign: 'positive' | 'negative'
}

/** A bar from zero to the effect; none for an effect that prints as ±0 pp. */
function bar(direction: AblationDirection, variant: AblationVariant | null): Bar | null {
  if (!variant || rounded(variant.effect_pp) === 0) return null
  const end = x(variant.effect_pp)
  return { direction, x: Math.min(50, end), width: Math.abs(end - 50), sign: variant.effect_pp > 0 ? 'positive' : 'negative' }
}

/**
 * One direction's numbers as the chart prints them beside its bar. The template keeps the
 * parts on one line, so they stay separate words when read as text.
 */
interface Value {
  direction: AblationDirection
  label: string
  effect: string
  counts: string
  harness: string | null
}

function printed(direction: AblationDirection, variant: AblationVariant | null): Value {
  const label = direction === 'forward' ? 'alone from A' : 'removed from B'
  if (!variant) return { direction, label, effect: 'not reported', counts: '', harness: null }
  return {
    direction,
    label,
    effect: formatEffect(variant.effect_pp),
    counts: `${variant.gained.length} gained, ${variant.lost.length} lost`,
    harness: variant.harness_errors ? plural(variant.harness_errors, 'harness error') : null,
  }
}

interface Row {
  dimension: ConfigDimension
  label: string
  paths: string[]
  forward: AblationVariant | null
  backward: AblationVariant | null
  bars: Bar[]
  values: Value[]
  /** Flipped cases this dimension alone is sufficient for, and necessary for. */
  sufficient: number
  necessary: number
}

const rows = computed<Row[]>(() =>
  props.ablation.dimensions.map((dimension) => {
    const forward = variantOf(props.ablation, dimension, 'forward')
    const backward = variantOf(props.ablation, dimension, 'backward')
    return {
      dimension,
      label: DIMENSION_LABELS[dimension],
      paths: forward?.paths ?? backward?.paths ?? [],
      forward,
      backward,
      bars: [bar('forward', forward), bar('backward', backward)].filter((b): b is Bar => b !== null),
      values: [printed('forward', forward), printed('backward', backward)],
      sufficient: props.ablation.cases.filter((c) => c.sufficient.includes(dimension)).length,
      necessary: props.ablation.cases.filter((c) => c.necessary.includes(dimension)).length,
    }
  }),
)

function toggle(dimension: ConfigDimension): void {
  emit('expand', props.expanded === dimension ? null : dimension)
}

const root = ref<HTMLElement | null>(null)
/** Closes the detail from inside it, handing focus back to the dimension that opened it. */
function collapse(): void {
  const dimension = props.expanded
  emit('expand', null)
  root.value?.querySelector<HTMLElement>(`[data-dimension="${dimension}"] button.dim`)?.focus()
}

// --- the expanded dimension ----------------------------------------------------------------

const detail = computed(() => {
  const row = rows.value.find((r) => r.dimension === props.expanded)
  if (!row) return null
  const name = dimensionName(row.dimension)
  const changes = props.diff.filter((d) => row.paths.some((p) => d.path === p || d.path.startsWith(`${p}.`)))
  const sides = (
    [
      {
        direction: 'forward',
        title: 'Alone from A',
        variant: row.forward,
        gained: `Gained: fail in A, pass with ${name}`,
        lost: `Lost: pass in A, fail with ${name}`,
      },
      {
        direction: 'backward',
        title: 'Removed from B',
        variant: row.backward,
        gained: `Gained: pass in B, fail without ${name}`,
        lost: `Lost: fail in B, pass without ${name}`,
      },
    ] as const
  ).map((side) => ({ ...side, name: variantName(side.direction, [row.dimension]) }))
  return { row, changes, sides }
})
const caseLink = (variant: AblationVariant, caseId: string) =>
  `/evaluations/${encodeURIComponent(variant.suite_id)}/cases/${encodeURIComponent(caseId)}`
</script>

<template>
  <div ref="root" class="effects">
    <template v-if="view === 'chart'">
      <ul class="legend" aria-label="Legend">
        <li><span class="swatch forward"></span>alone from A: A + d minus A</li>
        <li><span class="swatch backward"></span>removed from B: B minus B − d</li>
        <li><span class="swatch total"></span>total change, B − A ({{ formatEffect(ablation.total_effect_pp) }})</li>
        <li>Right of zero, the dimension helps; left, it hurts.</li>
      </ul>
      <ul class="rows" aria-label="Effect of each dimension on the pass rate">
        <li v-for="row in rows" :key="row.dimension" class="row" :data-dimension="row.dimension">
          <span class="label">
            <button
              type="button"
              class="dim"
              :aria-expanded="expanded === row.dimension"
              :aria-controls="expanded === row.dimension ? detailId : undefined"
              @click="toggle(row.dimension)"
            >
              <Icon :name="expanded === row.dimension ? 'chevronDown' : 'chevronRight'" :size="12" />
              {{ row.label }}
            </button>
            <span class="paths mono faint">{{ row.paths.join(', ') }}</span>
          </span>
          <svg class="bars" :viewBox="`0 0 100 ${ROW_H}`" preserveAspectRatio="none" aria-hidden="true">
            <line v-for="t in ticks" :key="t" class="grid" :class="{ zero: t === 0 }" :x1="x(t)" :x2="x(t)" y1="0" :y2="ROW_H" />
            <line class="total" :x1="totalX" :x2="totalX" y1="0" :y2="ROW_H" />
            <rect
              v-for="b in row.bars"
              :key="b.direction"
              :class="['bar', b.direction]"
              :data-direction="b.direction"
              :data-sign="b.sign"
              :x="b.x"
              :width="b.width"
              :y="BAR_Y[b.direction]"
              :height="BAR_H"
            />
          </svg>
          <span class="lines">
            <span v-for="value in row.values" :key="value.direction" class="line" :class="value.direction">
              <span class="direction">{{ value.label }}</span> <span class="mono effect">{{ value.effect }}</span> <span class="faint">{{ value.counts }}</span> <span v-if="value.harness" class="harness">{{ value.harness }}</span>
            </span>
          </span>
        </li>
        <li class="row axis" aria-hidden="true">
          <span></span>
          <span class="ticks">
            <span v-for="t in ticks" :key="t" class="tick mono" :style="{ left: `${x(t)}%` }">{{ tickLabel(t) }}</span>
          </span>
          <span></span>
        </li>
      </ul>
    </template>

    <div v-else class="table-wrap panel">
      <table class="data-table">
        <caption class="visually-hidden">Effect of each dimension on the pass rate, alone from A and removed from B</caption>
        <colgroup><col /></colgroup>
        <colgroup span="3"></colgroup>
        <colgroup span="3"></colgroup>
        <colgroup span="2"></colgroup>
        <thead>
          <tr>
            <th scope="col" rowspan="2">Dimension</th>
            <th scope="colgroup" colspan="3" class="group">Alone from A (A + d)</th>
            <th scope="colgroup" colspan="3" class="group">Removed from B (B − d)</th>
            <th scope="colgroup" colspan="2" class="group">Flipped cases</th>
          </tr>
          <tr>
            <th scope="col" class="num">Passed</th>
            <th scope="col" class="num">Effect</th>
            <th scope="col" class="num">Gained / lost</th>
            <th scope="col" class="num">Passed</th>
            <th scope="col" class="num">Effect</th>
            <th scope="col" class="num">Gained / lost</th>
            <th scope="col" class="num">Sufficient for</th>
            <th scope="col" class="num">Necessary for</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in rows" :key="row.dimension" :data-dimension="row.dimension">
            <th scope="row">
              <button
                type="button"
                class="dim"
                :aria-expanded="expanded === row.dimension"
                :aria-controls="expanded === row.dimension ? detailId : undefined"
                @click="toggle(row.dimension)"
              >
                <Icon :name="expanded === row.dimension ? 'chevronDown' : 'chevronRight'" :size="12" />
                {{ row.label }}
              </button>
              <span class="paths mono faint">{{ row.paths.join(', ') }}</span>
            </th>
            <template v-for="direction in DIRECTIONS" :key="direction">
              <template v-if="row[direction]">
                <td class="num mono" :data-col="`${direction}-passed`">
                  {{ ratio(row[direction]!.passed, row[direction]!.total) }}
                  <span v-if="row[direction]!.harness_errors" class="harness">
                    {{ plural(row[direction]!.harness_errors, 'harness error') }}
                  </span>
                </td>
                <td class="num mono effect" :data-col="`${direction}-effect`">{{ formatEffect(row[direction]!.effect_pp) }}</td>
                <td class="num mono" :data-col="`${direction}-counts`">
                  {{ row[direction]!.gained.length }} / {{ row[direction]!.lost.length }}
                </td>
              </template>
              <td v-else colspan="3" class="faint">not reported</td>
            </template>
            <td class="num mono" data-col="sufficient">{{ row.sufficient }}</td>
            <td class="num mono" data-col="necessary">{{ row.necessary }}</td>
          </tr>
        </tbody>
      </table>
    </div>

    <section v-if="detail" :id="detailId" class="dimension-detail panel" :aria-labelledby="`${detailId}-title`">
      <div class="detail-head">
        <h4 :id="`${detailId}-title`">{{ detail.row.label }}</h4>
        <span class="faint">
          enough alone for {{ plural(detail.row.sufficient, 'flipped case') }}, needed in B for
          {{ plural(detail.row.necessary, 'flipped case') }}
        </span>
        <button type="button" class="btn ghost close" @click="collapse">Collapse</button>
      </div>
      <table v-if="detail.changes.length" class="data-table changes">
        <caption class="visually-hidden">Configuration values this dimension swaps</caption>
        <thead>
          <tr>
            <th scope="col">Path</th>
            <th scope="col">A</th>
            <th scope="col">B</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="change in detail.changes" :key="change.path">
            <th scope="row" class="mono path">{{ change.path }}</th>
            <td class="mono swapped">{{ compact(change.a, 80) }}</td>
            <td class="mono swapped">{{ compact(change.b, 80) }}</td>
          </tr>
        </tbody>
      </table>
      <div class="sides">
        <div v-for="side in detail.sides" :key="side.direction" class="side" :data-direction="side.direction">
          <h5>{{ side.title }} · <span class="mono">{{ side.name }}</span></h5>
          <dl v-if="side.variant">
            <div>
              <dt>Evaluation</dt>
              <dd>
                <RouterLink :to="`/evaluations/${encodeURIComponent(side.variant.suite_id)}`" class="mono">
                  {{ side.variant.agent_ref }}
                </RouterLink>
              </dd>
            </div>
            <div>
              <dt>Passed</dt>
              <dd class="mono">
                {{ ratio(side.variant.passed, side.variant.total) }} · {{ formatEffect(side.variant.effect_pp) }}
                <span v-if="side.variant.harness_errors" class="harness">
                  {{ plural(side.variant.harness_errors, 'harness error') }}
                </span>
              </dd>
            </div>
            <div v-for="list in (['gained', 'lost'] as const)" :key="list">
              <dt>{{ side[list] }}</dt>
              <dd>
                <ul v-if="side.variant[list].length" class="case-links">
                  <li v-for="caseId in side.variant[list]" :key="caseId">
                    <RouterLink :to="caseLink(side.variant, caseId)" class="mono">{{ caseId }}</RouterLink>
                  </li>
                </ul>
                <span v-else class="faint">none</span>
              </dd>
            </div>
          </dl>
          <p v-else class="faint">Not reported.</p>
        </div>
      </div>
    </section>
  </div>
</template>

<style scoped>
.effects {
  display: grid;
  gap: 8px;
}
.legend,
.rows {
  margin: 0;
  padding: 0;
  list-style: none;
}
.legend {
  display: flex;
  flex-wrap: wrap;
  gap: 4px 16px;
  font-size: 12px;
  color: var(--text-muted);
}
.legend li {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}
.swatch {
  width: 16px;
  height: 9px;
}
.swatch.forward {
  background: var(--series-1);
}
.swatch.backward {
  border: 1.5px solid var(--series-2);
  background: color-mix(in srgb, var(--series-2) 22%, transparent);
}
.swatch.total {
  width: 0;
  height: 12px;
  border-left: 2px dashed var(--text-muted);
}
.row {
  display: grid;
  grid-template-columns: 210px minmax(160px, 1fr) 290px;
  align-items: center;
  gap: 12px;
}
.label {
  display: grid;
  min-width: 0;
}
.dim {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  justify-self: start;
  padding: 0 4px 0 0;
  border: 0;
  border-radius: var(--radius-sm);
  background: none;
  color: var(--text);
  font-size: 13px;
  font-weight: 500;
  text-align: left;
  cursor: pointer;
}
.dim:hover {
  color: var(--accent);
}
.dim[aria-expanded='true'] {
  color: var(--accent);
}
.paths {
  overflow: hidden;
  padding-left: 16px;
  font-size: 11px;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.bars {
  display: block;
  width: 100%;
  height: 36px;
  background: var(--surface-2);
}
.grid {
  stroke: var(--border-strong);
  stroke-width: 1;
  vector-effect: non-scaling-stroke;
}
.grid.zero {
  stroke: var(--text-muted);
  stroke-width: 1.5;
}
.total {
  stroke: var(--text-muted);
  stroke-width: 1.5;
  stroke-dasharray: 4 3;
  vector-effect: non-scaling-stroke;
}
.bar.forward {
  fill: var(--series-1);
}
.bar.backward {
  fill: var(--series-2);
  fill-opacity: 0.22;
  stroke: var(--series-2);
  stroke-width: 1.5;
  vector-effect: non-scaling-stroke;
}
.lines {
  display: grid;
  gap: 2px;
  font-size: 12px;
}
.line {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: 0 6px;
}
.direction {
  min-width: 92px;
  color: var(--text-muted);
}
.effect {
  font-weight: 600;
  color: var(--text);
}
.harness {
  color: var(--warn);
  font-family: var(--font-ui);
  font-size: 11.5px;
}
.axis .ticks {
  position: relative;
  height: 16px;
}
.tick {
  position: absolute;
  top: 0;
  transform: translateX(-50%);
  font-size: 10.5px;
  color: var(--text-muted);
  white-space: nowrap;
}
.tick:first-child {
  transform: none;
}
.tick:last-child {
  transform: translateX(-100%);
}
.rows {
  display: grid;
  gap: 4px;
}
th .dim {
  font-weight: 500;
}
th .paths {
  display: block;
  font-weight: 400;
}
thead .group {
  text-align: center;
  border-left: 1px solid var(--border);
}
.data-table td.effect {
  font-weight: 600;
}
.dimension-detail {
  display: grid;
  gap: 10px;
  padding: 10px 14px 12px;
}
.detail-head {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: 4px 12px;
  font-size: 12.5px;
}
.detail-head h4 {
  margin: 0;
  font-size: 14px;
}
.close {
  margin-left: auto;
}
.changes {
  border: 1px solid var(--border);
}
.changes .path {
  font-weight: 400;
  white-space: nowrap;
}
.changes .swapped {
  overflow-wrap: anywhere;
}
.sides {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 16px;
}
.side h5 {
  margin: 0 0 6px;
  font-size: 12.5px;
  font-weight: 600;
}
.side dl {
  display: grid;
  gap: 6px;
  margin: 0;
}
.side dt {
  font-size: 11px;
  color: var(--text-muted);
}
.side dd {
  margin: 1px 0 0;
  font-size: 12.5px;
}
.side p {
  margin: 0;
  font-size: 12.5px;
}
.case-links {
  display: flex;
  flex-wrap: wrap;
  gap: 2px 10px;
  margin: 0;
  padding: 0;
  list-style: none;
}
</style>
