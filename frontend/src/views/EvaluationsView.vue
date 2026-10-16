<script setup lang="ts">
import { computed } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '@/api/client'
import type { JobAccepted } from '@/api/types'
import StartEvaluationDialog from '@/components/evaluation/StartEvaluationDialog.vue'
import SuiteTable from '@/components/evaluation/SuiteTable.vue'
import EmptyState from '@/components/ui/EmptyState.vue'
import ErrorState from '@/components/ui/ErrorState.vue'
import Skeleton from '@/components/ui/Skeleton.vue'
import { plural } from '@/composables/format'
import { param, useQueryState } from '@/composables/useQueryState'
import { useResource } from '@/composables/useResource'

const router = useRouter()
const evaluations = useResource((signal) => api.listEvaluations(signal))
const { state: query, set } = useQueryState({
  dataset: param.string(),
  agent: param.string(),
  /** A dataset name@version to start an evaluation of ("Evaluate this dataset"). */
  new: param.string(),
  /** Set (to any value) while the start dialog is open without a preselected dataset. */
  start: param.boolean(),
})

const all = computed(() => evaluations.data.value ?? [])
const datasetOptions = computed(() => [...new Set(all.value.map((s) => `${s.dataset_name}@${s.dataset_version}`))].sort())
const agentOptions = computed(() => [...new Set(all.value.map((s) => s.agent_ref))].sort())
const suites = computed(() =>
  all.value.filter(
    (s) =>
      (!query.value.dataset || `${s.dataset_name}@${s.dataset_version}` === query.value.dataset) &&
      (!query.value.agent || s.agent_ref === query.value.agent),
  ),
)
const filtered = computed(() => Boolean(query.value.dataset || query.value.agent))
const dialogOpen = computed(() => Boolean(query.value.new) || query.value.start === true)

function closeDialog(): void {
  void set({ new: null, start: null })
}

async function onStarted(job: JobAccepted): Promise<void> {
  await router.push({
    path: `/evaluations/${encodeURIComponent(job.resource_id)}`,
    query: { job: job.job_id },
  })
}

function onFilter(key: 'dataset' | 'agent', event: Event): void {
  void set({ [key]: (event.target as HTMLSelectElement).value || null })
}
</script>

<template>
  <div class="screen">
    <header class="screen-head">
      <div>
        <h1 tabindex="-1">Evaluations</h1>
        <p class="subtitle">One agent version run over every case of a dataset version, with its checks and gate.</p>
      </div>
      <div class="actions">
        <button type="button" class="btn primary" @click="set({ start: true }, { push: true })">Start evaluation</button>
      </div>
    </header>

    <ErrorState v-if="evaluations.error.value && !evaluations.data.value" :error="evaluations.error.value" title="Could not load the evaluations" @retry="evaluations.reload" />
    <Skeleton v-else-if="!evaluations.data.value" :rows="6" />
    <EmptyState v-else-if="!all.length" title="No evaluations yet" variant="first-run">
      <p>Start one here, load the demo from the Overview, or run one from the command line:</p>
      <p><code>agentscope eval --dataset research-assistant --agent research_assistant@v2</code></p>
      <template #actions>
        <button type="button" class="btn primary" @click="set({ start: true }, { push: true })">Start evaluation</button>
      </template>
    </EmptyState>

    <template v-else>
      <form class="filters" aria-label="Filter evaluations" @submit.prevent>
        <label class="field">
          <span>Dataset</span>
          <select class="select mono-select" :value="query.dataset ?? ''" @change="onFilter('dataset', $event)">
            <option value="">All datasets</option>
            <option v-for="d in datasetOptions" :key="d" :value="d">{{ d }}</option>
          </select>
        </label>
        <label class="field">
          <span>Agent version</span>
          <select class="select mono-select" :value="query.agent ?? ''" @change="onFilter('agent', $event)">
            <option value="">All agent versions</option>
            <option v-for="a in agentOptions" :key="a" :value="a">{{ a }}</option>
          </select>
        </label>
        <button v-if="filtered" type="button" class="btn ghost" @click="set({ dataset: null, agent: null })">Clear filters</button>
        <span class="faint count">{{ plural(suites.length, 'evaluation') }}</span>
      </form>
      <EmptyState v-if="!suites.length" title="No evaluations match these filters" variant="filtered">
        <template #actions>
          <button type="button" class="btn" @click="set({ dataset: null, agent: null })">Clear filters</button>
        </template>
      </EmptyState>
      <SuiteTable v-else :suites="suites" caption="Evaluations" />
    </template>

    <StartEvaluationDialog :open="dialogOpen" :dataset="query.new" @close="closeDialog" @started="onStarted" />
  </div>
</template>

<style scoped>
.filters {
  display: flex;
  flex-wrap: wrap;
  align-items: flex-end;
  gap: 12px;
}
.mono-select {
  font-family: var(--font-mono);
  font-size: 12px;
}
.count {
  margin-left: auto;
  padding-bottom: 5px;
  font-size: 12.5px;
}
</style>
