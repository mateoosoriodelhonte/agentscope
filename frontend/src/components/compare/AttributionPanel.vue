<script setup lang="ts">
import { computed, ref } from 'vue'
import { api } from '@/api/client'
import { describeError } from '@/api/errors'
import type { Comparison, ConfigDimension } from '@/api/types'
import { MAX_PAIRWISE_DIMENSIONS } from '@/api/types'
import ErrorState from '@/components/ui/ErrorState.vue'
import Icon from '@/components/ui/Icon.vue'
import IssueList from '@/components/ui/IssueList.vue'
import Metric from '@/components/ui/Metric.vue'
import SimulatedBadge from '@/components/ui/SimulatedBadge.vue'
import Skeleton from '@/components/ui/Skeleton.vue'
import ViewToggle from '@/components/ui/ViewToggle.vue'
import { compact, formatTimestamp, plural, ratio } from '@/composables/format'
import { jobProgress, useJob } from '@/composables/useJob'
import { useResource } from '@/composables/useResource'
import { ablatableDimensions, ablationPlan, dimensionName, formatEffect, overallReading } from './attribution'
import CaseAttributions from './CaseAttributions.vue'
import DimensionEffects from './DimensionEffects.vue'
import PairInteractions from './PairInteractions.vue'

/*
 * Attribution of a comparison's change to its configuration dimensions: an ablation evaluates
 * the same dataset version with one dimension changed at a time, so each change gets its own
 * effect and the part that only appears in combination is measured, not guessed. It shows
 * the stored ablation of this comparison when there is one, and otherwise offers to run it.
 * Mount it only when the comparison has an ablatable dimension (see ablatableDimensions).
 */
const props = defineProps<{
  comparison: Comparison
  /** The dimension whose variants are shown in detail (URL state). */
  expanded: ConfigDimension | null
  view: 'chart' | 'table'
}>()
const emit = defineEmits<{ expand: [dimension: ConfigDimension | null]; 'update:view': [view: 'chart' | 'table'] }>()

/** What attribution does and what it will run, in one sentence. */
const offer = computed(() => {
  const { dataset_name: name, dataset_version: version, config_diff: diff } = props.comparison
  const n = ablatableDimensions(diff).length
  const plan = ablationPlan(n)
  const each = n === 1 ? 'the changed dimension gets' : `each of the ${n} changed dimensions gets`
  const pairs = plan.pairs
    ? `, plus ${plural(plan.pairs, 'pair')} (${n} choose 2) to locate interactions`
    : plan.pairsSkipped
      ? ` (pairs are only evaluated up to ${MAX_PAIRWISE_DIMENSIONS} dimensions)`
      : ''
  return `Attribution evaluates ${name}@${version} again with one dimension changed at a time, so ${each} its own effect: 2 × ${n} variant evaluations${pairs}.`
})

const existing = useResource((signal) =>
  api.listAblations({ experiment_id: props.comparison.experiment_id, limit: 1 }, signal),
)
const runner = useJob()
/** The ablation this panel started, which wins over the one found stored. */
const started = ref<string | null>(null)
const ablationId = computed(() => started.value ?? existing.data.value?.items[0]?.ablation_id ?? null)
const ablation = useResource(
  (signal) => (ablationId.value ? api.getAblation(ablationId.value, signal) : Promise.resolve(null)),
  () => ablationId.value,
)
const data = computed(() => ablation.data.value)
const startError = computed(() => (runner.error.value ? describeError(runner.error.value) : null))
const progress = computed(() => {
  const job = runner.job.value
  return job && job.total > 0 ? { value: job.progress, max: job.total } : null
})

async function attribute(): Promise<void> {
  const done = await runner.start(() => api.createAblation({ experiment_id: props.comparison.experiment_id }))
  if (done) started.value = done.resource_id
}
</script>

<template>
  <section id="attribution" class="attribution section" aria-labelledby="attribution-title">
    <div class="section-head">
      <h2 id="attribution-title" class="section-title">Attribution</h2>
      <div v-if="data" class="actions">
        <SimulatedBadge v-if="data.deterministic" compact />
      </div>
    </div>

    <div v-if="runner.running.value" class="callout info running" role="status">
      <Icon name="clock" :size="14" />
      <div>
        <p>Attributing the change… {{ jobProgress(runner.job.value) }}</p>
        <progress v-if="progress" :value="progress.value" :max="progress.max" aria-label="Attribution progress"></progress>
        <progress v-else aria-label="Attribution progress"></progress>
      </div>
    </div>
    <div v-else-if="startError" class="callout fail" role="alert">
      <Icon name="alert" :size="14" />
      <div>
        <p>Could not attribute the change: {{ startError.message }}</p>
        <IssueList v-if="startError.issues.length" :issues="startError.issues" />
        <button type="button" class="btn retry" @click="attribute">Try again</button>
      </div>
    </div>

    <template v-else-if="ablationId">
      <ErrorState
        v-if="ablation.error.value && !data"
        :error="ablation.error.value"
        title="Could not load the attribution"
        compact
        @retry="ablation.reload"
      />
      <Skeleton v-else-if="!data" :rows="4" />
      <template v-else>
        <dl class="summary">
          <Metric label="Total change (B − A)" :value="formatEffect(data.total_effect_pp)" />
          <Metric label="Sum of solo effects" :value="formatEffect(data.sum_of_solo_effects_pp)" />
          <Metric label="Interaction" :value="formatEffect(data.interaction_pp)" />
          <Metric
            label="Cases compared"
            :value="`n = ${data.n} · A ${ratio(data.a.metrics.passed, data.a.metrics.total)} → B ${ratio(data.b.metrics.passed, data.b.metrics.total)}`"
          />
        </dl>
        <p class="reading">{{ overallReading(data) }}</p>

        <p v-if="!data.deterministic" class="callout warn" role="note">
          <Icon name="alert" :size="14" />
          <span>
            At least one evaluation used a non-deterministic model: effects may be noise. Re-run the comparison and its
            attribution to confirm before trusting them.
          </span>
        </p>
        <div v-if="data.not_ablated.length" class="callout warn" role="note">
          <Icon name="alert" :size="14" />
          <div>
            <p>
              <strong>Not ablated.</strong> No configuration field expresses
              {{ data.not_ablated.length === 1 ? 'this difference' : 'these differences' }}, so
              {{ data.not_ablated.length === 1 ? 'it' : 'they' }} could not be changed alone and part of the change may come
              from {{ data.not_ablated.length === 1 ? 'it' : 'them' }}:
            </p>
            <ul class="not-ablated">
              <li v-for="d in data.not_ablated" :key="d.path">
                <span class="mono">{{ d.path }}</span> <span class="faint">({{ dimensionName(d.dimension) }})</span> <span class="mono">{{ compact(d.a, 40) }} → {{ compact(d.b, 40) }}</span>
              </li>
            </ul>
          </div>
        </div>
        <p v-for="(warning, i) in data.warnings" :key="`warning-${i}`" class="callout warn" role="note">
          <Icon name="alert" :size="14" />
          <span>{{ warning }}</span>
        </p>

        <section class="sub" aria-labelledby="effects-title">
          <div class="section-head">
            <h3 id="effects-title" class="sub-title">Effect of each dimension</h3>
            <div class="actions">
              <ViewToggle
                :model-value="view"
                label="Show the effect of each dimension as"
                @update:model-value="emit('update:view', $event)"
              />
            </div>
          </div>
          <DimensionEffects
            :ablation="data"
            :diff="comparison.config_diff"
            :expanded="expanded"
            :view="view"
            @expand="emit('expand', $event)"
          />
        </section>

        <section class="sub" aria-labelledby="interactions-title">
          <h3 id="interactions-title" class="sub-title">Interactions</h3>
          <PairInteractions :ablation="data" />
        </section>

        <section class="sub" aria-labelledby="case-attribution-title">
          <h3 id="case-attribution-title" class="sub-title">Flipped cases</h3>
          <CaseAttributions :ablation="data" />
        </section>

        <p class="faint footnote">
          {{ data.name }} · {{ data.dataset_name }}@{{ data.dataset_version }} · {{ formatTimestamp(data.created_at) }}
        </p>
      </template>
    </template>

    <ErrorState
      v-else-if="existing.error.value"
      :error="existing.error.value"
      title="Could not look for an attribution of this comparison"
      compact
      @retry="existing.reload"
    />
    <Skeleton v-else-if="!existing.data.value" :rows="2" />
    <div v-else class="callout info start">
      <Icon name="flask" :size="14" />
      <div>
        <p>{{ offer }}</p>
        <button type="button" class="btn primary" @click="attribute">Attribute the change</button>
      </div>
    </div>
  </section>
</template>

<style scoped>
.attribution {
  gap: 10px;
}
.callout p {
  margin: 0;
}
.callout > div {
  display: grid;
  gap: 6px;
  justify-items: start;
}
.running progress {
  width: min(320px, 100%);
}
.summary {
  display: flex;
  flex-wrap: wrap;
  gap: 8px 28px;
  margin: 0;
}
.summary :deep(.value) {
  font-size: 14px;
  font-weight: 600;
}
.reading {
  margin: 0;
  font-size: 13.5px;
}
.not-ablated {
  margin: 0;
  padding-left: 18px;
  font-size: 12.5px;
}
.not-ablated li {
  overflow-wrap: anywhere;
}
.sub {
  display: grid;
  grid-template-columns: minmax(0, 1fr);
  gap: 6px;
  margin-top: 4px;
}
.sub-title {
  font-size: 13px;
  font-weight: 600;
}
.footnote {
  margin: 0;
  font-size: 12px;
}
</style>
