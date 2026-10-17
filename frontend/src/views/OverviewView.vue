<script setup lang="ts">
import { computed } from 'vue'
import { RouterLink } from 'vue-router'
import { api } from '@/api/client'
import { describeError } from '@/api/errors'
import PassRateChart from '@/components/charts/PassRateChart.vue'
import GateChecklist from '@/components/evaluation/GateChecklist.vue'
import SuiteTable from '@/components/evaluation/SuiteTable.vue'
import ProviderPanel from '@/components/overview/ProviderPanel.vue'
import StartHereCard from '@/components/overview/StartHereCard.vue'
import EmptyState from '@/components/ui/EmptyState.vue'
import ErrorState from '@/components/ui/ErrorState.vue'
import Skeleton from '@/components/ui/Skeleton.vue'
import StatusBadge from '@/components/ui/StatusBadge.vue'
import ViewToggle from '@/components/ui/ViewToggle.vue'
import { formatTimestamp, plural } from '@/composables/format'
import { openNewRun } from '@/composables/newRun'
import { useJob, jobProgress } from '@/composables/useJob'
import { useLocalFlag } from '@/composables/useLocalFlag'
import { param, useQueryState } from '@/composables/useQueryState'
import { useResource } from '@/composables/useResource'

const overview = useResource((signal) => api.overview(signal))
const { state: query, set } = useQueryState({
  agent: param.string(),
  view: param.enum(['chart', 'table'] as const),
})
const dismissed = useLocalFlag('agentscope.overview.start-dismissed')
const seed = useJob()

async function loadDemo(): Promise<void> {
  const done = await seed.start(() => api.seedDemo())
  if (done) await overview.reload()
}
const seedProgress = computed(() =>
  seed.running.value ? `Seeding demo data… ${jobProgress(seed.job.value)}` : null,
)
const seedError = computed(() =>
  seed.error.value ? `Could not load the demo: ${describeError(seed.error.value).message}` : null,
)

const data = computed(() => overview.data.value)
const isEmpty = computed(() => {
  const d = data.value
  if (!d) return false
  return Object.values(d.counts).every((n) => n === 0) && !d.recent_runs.length && !d.recent_suites.length
})
const countsLine = computed(() => {
  const counts = data.value?.counts ?? {}
  const names: [string, string, string][] = [
    ['runs', 'run', 'runs'],
    ['suites', 'evaluation', 'evaluations'],
    ['datasets', 'dataset', 'datasets'],
    ['experiments', 'comparison', 'comparisons'],
  ]
  return names
    .filter(([key]) => key in counts)
    .map(([key, one, many]) => plural(counts[key] ?? 0, one, many))
    .join(' · ')
})

const agentRefs = computed(() => {
  const d = data.value
  if (!d) return []
  const refs = new Set([
    ...d.pass_rate_series.map((s) => s.agent_ref),
    ...d.recent_suites.map((s) => s.agent_ref),
    ...d.recent_runs.map((r) => `${r.agent}@${r.agent_version}`),
  ])
  if (query.value.agent) refs.add(query.value.agent)
  return [...refs].sort()
})
const suites = computed(() =>
  (data.value?.recent_suites ?? []).filter((s) => !query.value.agent || s.agent_ref === query.value.agent),
)
const runs = computed(() =>
  (data.value?.recent_runs ?? []).filter(
    (r) => !query.value.agent || `${r.agent}@${r.agent_version}` === query.value.agent,
  ),
)
const hasSeries = computed(() =>
  (data.value?.pass_rate_series ?? []).some(
    (s) => s.points.length && (!query.value.agent || s.agent_ref === query.value.agent),
  ),
)
const filteredToNothing = computed(
  () => Boolean(query.value.agent) && !hasSeries.value && !suites.value.length && !runs.value.length,
)
const threshold = computed(() => {
  const rule = data.value?.latest_gate?.rules.find((r) => r.name === 'min_pass_rate')
  return typeof rule?.threshold === 'number' ? rule.threshold : null
})

function onAgent(event: Event): void {
  void set({ agent: (event.target as HTMLSelectElement).value || null })
}
</script>

<template>
  <div class="screen">
    <header class="screen-head">
      <div>
        <h1 tabindex="-1">Overview</h1>
        <p v-if="data && !isEmpty" class="subtitle">{{ countsLine }}</p>
      </div>
      <div class="actions">
        <button type="button" class="btn" @click="openNewRun()">New run</button>
      </div>
    </header>

    <ErrorState v-if="overview.error.value && !data" :error="overview.error.value" title="Could not load the overview" @retry="overview.reload" />
    <Skeleton v-else-if="!data" :rows="8" />

    <template v-else>
      <StartHereCard
        v-if="!dismissed"
        :suites="data.recent_suites"
        :seeding="seed.running.value"
        :progress="seedProgress"
        :error="seedError"
        @dismiss="dismissed = true"
        @seed="loadDemo"
      />
      <p v-else class="restore">
        <button type="button" class="btn ghost" @click="dismissed = false">Show the demo walkthrough</button>
      </p>

      <EmptyState v-if="isEmpty" title="Nothing recorded yet" variant="first-run">
        <p>Load the demo story above, or run an agent and evaluate a dataset from the command line:</p>
        <p><code>agentscope run --agent research_assistant@v2 --task "In what year did the Meridian Footbridge open?" --expect 1987</code></p>
        <p><code>agentscope eval --dataset research-assistant --agent research_assistant@v2</code></p>
      </EmptyState>

      <template v-else>
        <form class="filters" aria-label="Filter the overview" @submit.prevent>
          <label class="field">
            <span>Agent version</span>
            <select class="select mono-select" :value="query.agent ?? ''" @change="onAgent">
              <option value="">All agent versions</option>
              <option v-for="agentRef in agentRefs" :key="agentRef" :value="agentRef">{{ agentRef }}</option>
            </select>
          </label>
          <button v-if="query.agent" type="button" class="btn ghost" @click="set({ agent: null })">Clear filter</button>
        </form>

        <EmptyState v-if="filteredToNothing" title="Nothing recorded for this agent version" variant="filtered">
          <p>No evaluation or recent run used <code>{{ query.agent }}</code>.</p>
          <template #actions>
            <button type="button" class="btn" @click="set({ agent: null })">Clear filter</button>
          </template>
        </EmptyState>

        <template v-else>
          <section class="section" aria-labelledby="trend-title">
            <div class="section-head">
              <h2 id="trend-title" class="section-title">Pass rate by agent version</h2>
              <div class="actions">
                <ViewToggle
                  :model-value="query.view ?? 'chart'"
                  label="Show the pass rate as"
                  @update:model-value="set({ view: $event === 'chart' ? null : $event })"
                />
              </div>
            </div>
            <PassRateChart
              v-if="hasSeries"
              :series="data.pass_rate_series"
              :only="query.agent"
              :threshold="threshold"
              :view="query.view ?? 'chart'"
            />
            <p v-else class="faint none">No finished evaluation yet: the trend appears after the first one.</p>
          </section>

          <div class="columns">
            <GateChecklist :gate="data.latest_gate" heading="Latest gate" />
            <ProviderPanel :providers="data.providers" />
          </div>

          <section class="section" aria-labelledby="suites-title">
            <div class="section-head">
              <h2 id="suites-title" class="section-title">Recent evaluations</h2>
              <div class="actions"><RouterLink to="/evaluations" class="btn ghost">All evaluations</RouterLink></div>
            </div>
            <SuiteTable v-if="suites.length" :suites="suites" caption="Recent evaluations" />
            <p v-else class="faint none">No evaluations yet.</p>
          </section>

          <section class="section" aria-labelledby="runs-title">
            <div class="section-head">
              <h2 id="runs-title" class="section-title">Recent runs</h2>
              <div class="actions"><RouterLink to="/runs" class="btn ghost">All runs</RouterLink></div>
            </div>
            <div v-if="runs.length" class="table-wrap panel">
              <table class="data-table">
                <caption class="visually-hidden">Recent runs</caption>
                <thead>
                  <tr>
                    <th scope="col">Status</th>
                    <th scope="col">Task</th>
                    <th scope="col">Agent</th>
                    <th scope="col">Verdict</th>
                    <th scope="col">Started</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="run in runs" :key="run.run_id">
                    <td><StatusBadge :status="run.status" /></td>
                    <td class="task">
                      <RouterLink :to="`/runs/${encodeURIComponent(run.run_id)}`">{{ run.task }}</RouterLink>
                    </td>
                    <td class="mono">{{ run.agent }}@{{ run.agent_version }}</td>
                    <td>
                      <StatusBadge v-if="run.passed !== null" :status="run.passed ? 'passed' : 'failed'" variant="plain" />
                      <span v-else class="faint">not evaluated</span>
                    </td>
                    <td class="faint when">{{ formatTimestamp(run.started_at) }}</td>
                  </tr>
                </tbody>
              </table>
            </div>
            <p v-else class="faint none">No runs yet.</p>
          </section>
        </template>
      </template>
    </template>
  </div>
</template>

<style scoped>
.restore {
  margin: -8px 0 0;
}
.filters {
  display: flex;
  align-items: flex-end;
  gap: 10px;
}
.mono-select {
  font-family: var(--font-mono);
  font-size: 12px;
}
.columns {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(260px, 320px);
  gap: 18px;
  align-items: start;
}
.none {
  margin: 0;
  font-size: 13px;
}
.task {
  max-width: 0;
  width: 50%;
}
.task a {
  display: block;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--text);
}
.task a:hover {
  color: var(--accent);
}
.when {
  font-size: 12px;
  white-space: nowrap;
}
</style>
