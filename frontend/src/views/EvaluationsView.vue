<script setup lang="ts">
import { computed, watch } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '@/api/client'
import type { JobAccepted, SuiteListItem } from '@/api/types'
import StartEvaluationDialog from '@/components/evaluation/StartEvaluationDialog.vue'
import SuiteTable from '@/components/evaluation/SuiteTable.vue'
import EmptyState from '@/components/ui/EmptyState.vue'
import ErrorState from '@/components/ui/ErrorState.vue'
import Skeleton from '@/components/ui/Skeleton.vue'
import { plural } from '@/composables/format'
import { pageInfo } from '@/composables/paging'
import { param, useQueryState } from '@/composables/useQueryState'
import { useResource } from '@/composables/useResource'

/*
 * Every evaluation, newest first, a page at a time. Filters go to the API, so older suites
 * are found too. The dataset filter names a version, which the API cannot filter on (it
 * filters by dataset name), so the version is checked here and hidden rows are counted.
 */
const PAGE_SIZE = 50

const router = useRouter()
const { state: query, set } = useQueryState({
  dataset: param.string(),
  agent: param.string(),
  page: param.int(1, 1),
  /** A dataset name@version to start an evaluation of ("Evaluate this dataset"). */
  new: param.string(),
  /** Set (to any value) while the start dialog is open without a preselected dataset. */
  start: param.boolean(),
})

/** The dataset name of a name@version filter (names never contain "@"). */
function datasetName(ref: string | null): string | undefined {
  if (!ref) return undefined
  const at = ref.lastIndexOf('@')
  return at > 0 ? ref.slice(0, at) : ref
}

const evaluations = useResource((signal) => {
  const q = query.value
  return api.listEvaluations(
    {
      agent_ref: q.agent ?? undefined,
      dataset: datasetName(q.dataset),
      limit: PAGE_SIZE,
      offset: (q.page - 1) * PAGE_SIZE,
    },
    signal,
  )
})
// A new filter or page reloads in place: the old rows (and the focused filter) stay meanwhile.
watch(
  () => [query.value.dataset, query.value.agent, query.value.page] as const,
  () => void evaluations.reload(),
)
// Filter choices: every stored dataset version and agent version, not only this page's.
const datasets = useResource((signal) => api.listDatasets(signal))
const catalog = useResource((signal) => api.agents(signal))

/** Checks the version (and any filter a server ignored): rows shown always match. */
function matches(s: SuiteListItem): boolean {
  const q = query.value
  return (
    (!q.dataset || `${s.dataset_name}@${s.dataset_version}` === q.dataset) &&
    (!q.agent || s.agent_ref === q.agent)
  )
}

const received = computed(() => evaluations.data.value?.items ?? [])
const suites = computed(() => received.value.filter(matches))
const dropped = computed(() => received.value.length - suites.value.length)
const total = computed(() => evaluations.data.value?.total ?? null)
const paging = computed(() => pageInfo(query.value.page, PAGE_SIZE, received.value.length, total.value))
const filtered = computed(() => Boolean(query.value.dataset || query.value.agent))
const firstRun = computed(() => !filtered.value && query.value.page === 1 && !received.value.length)
const countLabel = computed(() => {
  if (total.value === null) return `${plural(suites.value.length, 'evaluation')} on this page`
  if (dropped.value) return `${plural(total.value, 'evaluation')} before checking the dataset version`
  return plural(total.value, 'evaluation')
})

function withSelected(values: Iterable<string>, selected: string | null): string[] {
  const all = new Set(values)
  if (selected) all.add(selected)
  return [...all].sort()
}
const datasetOptions = computed(() =>
  withSelected(
    [
      ...(datasets.data.value ?? []).map((d) => `${d.name}@${d.version}`),
      ...received.value.map((s) => `${s.dataset_name}@${s.dataset_version}`),
    ],
    query.value.dataset,
  ),
)
const agentOptions = computed(() =>
  withSelected(
    [
      ...(catalog.data.value?.agents ?? []).flatMap((a) => a.versions.map((v) => `${v.agent}@${v.version}`)),
      ...received.value.map((s) => s.agent_ref),
    ],
    query.value.agent,
  ),
)
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
  void set({ [key]: (event.target as HTMLSelectElement).value || null, page: 1 })
}

function clearFilters(): void {
  void set({ dataset: null, agent: null, page: 1 })
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
    <EmptyState v-else-if="firstRun" title="No evaluations yet" variant="first-run">
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
        <button v-if="filtered" type="button" class="btn ghost" @click="clearFilters">Clear filters</button>
        <span class="faint count">{{ countLabel }}</span>
      </form>
      <p v-if="dropped" class="notice" role="note">
        {{ plural(dropped, 'evaluation') }} of another dataset version on this page
        {{ dropped === 1 ? 'is' : 'are' }} hidden.
      </p>
      <EmptyState v-if="!suites.length && filtered && paging.complete" title="No evaluations match these filters" variant="filtered">
        <template #actions>
          <button type="button" class="btn" @click="clearFilters">Clear filters</button>
        </template>
      </EmptyState>
      <p v-else-if="!suites.length" class="notice empty-page" role="status">
        {{ dropped ? 'No evaluation on this page matches every filter; other pages may.' : 'No evaluations on this page.' }}
        <button v-if="query.page > 1" type="button" class="btn ghost" @click="set({ page: 1 })">First page</button>
      </p>
      <SuiteTable v-else :suites="suites" caption="Evaluations" :aria-busy="evaluations.loading.value" />
      <nav v-if="paging.hasPrevious || paging.hasNext" class="pager" aria-label="Pagination">
        <span class="muted">{{ paging.range }}</span>
        <button type="button" class="btn" :disabled="!paging.hasPrevious" @click="set({ page: query.page - 1 })">Previous</button>
        <button type="button" class="btn" :disabled="!paging.hasNext" @click="set({ page: query.page + 1 })">Next</button>
      </nav>
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
.notice {
  margin: 0;
  font-size: 12.5px;
  color: var(--warn);
}
.empty-page {
  display: flex;
  align-items: center;
  gap: 8px;
  color: var(--text-muted);
}
.pager {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 8px;
  font-size: 12.5px;
}
</style>
