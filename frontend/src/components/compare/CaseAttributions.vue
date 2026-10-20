<script setup lang="ts">
import { computed } from 'vue'
import type { Ablation, CaseAttribution, ConfigDimension } from '@/api/types'
import StatusBadge from '@/components/ui/StatusBadge.vue'
import type { Outcome } from '@/composables/useTraceModel'
import { pairKey, variantName } from './attribution'
import AttributionChip from './AttributionChip.vue'
import { DIMENSION_LABELS } from './dimensions'

/*
 * For every case whose verdict differs between A and B: the dimensions that alone carry A to
 * B's verdict (sufficient), the ones B cannot do without (necessary), and, when no single one
 * is enough, the evaluated pairs that are. Regressions come first, as in the case list.
 */
const props = defineProps<{ ablation: Ablation }>()

const rows = computed(() =>
  [...props.ablation.cases].sort((x, y) => Number(x.change !== 'regression') - Number(y.change !== 'regression')),
)
const CHANGE: Record<string, Outcome> = {
  regression: { tone: 'fail', label: 'regressed' },
  fix: { tone: 'ok', label: 'fixed' },
}
const changeOf = (c: CaseAttribution): Outcome => CHANGE[c.change] ?? { tone: 'na', label: c.change.replace(/_/g, ' ') }
const run = (c: CaseAttribution, key: string) => c.runs[key] ?? null
const pairLabel = (pair: ConfigDimension[]) => pair.map((d) => DIMENSION_LABELS[d]).join(' + ')
</script>

<template>
  <div class="case-attribution">
    <ul class="legend" aria-label="Legend">
      <li><span class="swatch solid"></span>enough alone: A + d already gives B's verdict</li>
      <li><span class="swatch outlined"></span>needed: B − d gives back A's verdict</li>
      <li>Each chip opens that variant's run; the arrows open its trace diff against A.</li>
    </ul>
    <div v-if="rows.length" class="table-wrap panel">
      <table class="data-table">
        <caption class="visually-hidden">The dimensions each flipped case depends on</caption>
        <thead>
          <tr>
            <th scope="col">Case</th>
            <th scope="col">Change</th>
            <th scope="col">Enough alone (A + d)</th>
            <th scope="col">Needed in B (B − d)</th>
            <th scope="col">Combination</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="c in rows" :key="c.case_id" :data-case="c.case_id">
            <th scope="row" class="mono case">{{ c.case_id }}</th>
            <td><StatusBadge :outcome="changeOf(c)" variant="plain" /></td>
            <td class="sufficient">
              <ul v-if="c.sufficient.length" class="chips">
                <li v-for="d in c.sufficient" :key="d">
                  <AttributionChip
                    :label="DIMENSION_LABELS[d]"
                    :variant="variantName('forward', [d])"
                    kind="solid"
                    :case-id="c.case_id"
                    :run-id="run(c, `forward:${d}`)"
                    :a-run-id="run(c, 'a')"
                  />
                </li>
              </ul>
              <span v-else class="faint">none</span>
            </td>
            <td class="necessary">
              <ul v-if="c.necessary.length" class="chips">
                <li v-for="d in c.necessary" :key="d">
                  <AttributionChip
                    :label="DIMENSION_LABELS[d]"
                    :variant="variantName('backward', [d])"
                    kind="outlined"
                    :case-id="c.case_id"
                    :run-id="run(c, `backward:${d}`)"
                    :a-run-id="run(c, 'a')"
                  />
                </li>
              </ul>
              <span v-else class="faint">none</span>
            </td>
            <td class="combination">
              <ul v-if="c.needs_combination" class="chips">
                <li><span class="tag">needs combination</span></li>
                <li v-for="pair in c.sufficient_pairs" :key="pair.join('+')">
                  <AttributionChip
                    :label="pairLabel(pair)"
                    :variant="variantName('forward', pair)"
                    kind="solid"
                    :case-id="c.case_id"
                    :run-id="run(c, pairKey(ablation, pair))"
                    :a-run-id="run(c, 'a')"
                  />
                </li>
                <li v-if="!c.sufficient_pairs.length" class="faint">
                  {{ ablation.pairs_skipped ? 'pairs not evaluated' : 'no evaluated pair is enough' }}
                </li>
              </ul>
              <span v-else class="faint">not needed</span>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
    <p v-else class="faint none">No case changed its verdict between A and B.</p>
  </div>
</template>

<style scoped>
.case-attribution {
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
.swatch {
  width: 18px;
  height: 10px;
  border: 1px solid var(--accent);
  border-radius: 999px;
}
.swatch.solid {
  background: var(--accent);
}
.case {
  font-weight: 400;
  overflow-wrap: anywhere;
}
.chips {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 4px;
  margin: 0;
  padding: 0;
  list-style: none;
  font-size: 12px;
}
.none {
  margin: 0;
  font-size: 12.5px;
}
</style>
