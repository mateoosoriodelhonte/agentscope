<script setup lang="ts">
import { computed } from 'vue'
import { RouterLink } from 'vue-router'
import type { Ablation, ConfigDimension } from '@/api/types'
import { MAX_PAIRWISE_DIMENSIONS } from '@/api/types'
import { ratio } from '@/composables/format'
import { formatEffect, interactions, variantName } from './attribution'
import { DIMENSION_LABELS } from './dimensions'

/*
 * Where the interaction is: each evaluated pair "A + d1 + d2" against its two solo effects.
 * Only pairs whose interaction is not zero are listed, the largest first, so two changes
 * that only work together stand out as one line.
 */
const props = defineProps<{ ablation: Ablation }>()

const rows = computed(() => interactions(props.ablation))
const quiet = computed(() => {
  const count = props.ablation.pairs.length - rows.value.length
  if (!rows.value.length || count === 0) return null
  return count === 1
    ? '1 other pair adds up to its two solo effects and is not listed.'
    : `${count} other pairs add up to their two solo effects and are not listed.`
})
const pairLabel = (dimensions: readonly ConfigDimension[]) => dimensions.map((d) => DIMENSION_LABELS[d]).join(' × ')
const solo = (value: number | null) => (value === null ? 'not reported' : formatEffect(value))
</script>

<template>
  <div class="pairs">
    <p v-if="ablation.pairs_skipped" class="callout info" role="note">
      Pairs were not evaluated (more than {{ MAX_PAIRWISE_DIMENSIONS }} dimensions, or pairwise analysis was off), so the
      interaction cannot be located.
    </p>
    <p v-else-if="ablation.dimensions.length < 2" class="faint note">A single dimension has no pairs.</p>
    <template v-else>
      <ol v-if="rows.length" class="list">
        <li v-for="row in rows" :key="row.pair.dimensions.join('+')" class="pair">
          <span class="headline">
            <strong>{{ pairLabel(row.pair.dimensions) }}</strong>:
            <span class="mono value">{{ formatEffect(row.pair.interaction_pp) }}</span>,
            {{ row.reading }}
          </span>
          <span class="detail faint">
            <RouterLink :to="`/evaluations/${encodeURIComponent(row.pair.suite_id)}`" class="mono">
              {{ variantName('forward', row.pair.dimensions) }}
            </RouterLink>
            {{ ratio(row.pair.passed, row.pair.total) }} ({{ formatEffect(row.pair.effect_pp) }} from A); alone
            {{ solo(row.solo[0]) }} and {{ solo(row.solo[1]) }}
          </span>
        </li>
      </ol>
      <p v-else class="faint note">No pair interacts: every pair adds up to its two solo effects.</p>
      <p v-if="quiet" class="faint note">{{ quiet }}</p>
    </template>
  </div>
</template>

<style scoped>
.pairs {
  display: grid;
  gap: 6px;
}
.list {
  display: grid;
  gap: 0;
  margin: 0;
  padding: 0;
  list-style: none;
  border: 1px solid var(--border);
  border-radius: var(--radius);
  background: var(--surface-1);
}
.pair {
  display: grid;
  gap: 2px;
  padding: 7px 10px;
  border-bottom: 1px solid var(--border);
  font-size: 13px;
}
.pair:last-child {
  border-bottom: 0;
}
.value {
  font-size: 12.5px;
  font-weight: 600;
}
.detail {
  font-size: 12px;
}
.note {
  margin: 0;
  font-size: 12.5px;
}
</style>
