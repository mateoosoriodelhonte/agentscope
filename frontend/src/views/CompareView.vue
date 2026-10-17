<script setup lang="ts">
import { computed, ref, shallowRef, watch } from 'vue'
import { RouterLink } from 'vue-router'
import { api } from '@/api/client'
import { describeError } from '@/api/errors'
import type { SuiteListItem } from '@/api/types'
import CheckRateBars from '@/components/compare/CheckRateBars.vue'
import ConfigDiff from '@/components/compare/ConfigDiff.vue'
import { CHECK_PREFIX } from '@/components/compare/deltas'
import FlipList from '@/components/compare/FlipList.vue'
import MetricDeltaTable from '@/components/compare/MetricDeltaTable.vue'
import TraceDiff from '@/components/compare/TraceDiff.vue'
import CopyButton from '@/components/ui/CopyButton.vue'
import ErrorState from '@/components/ui/ErrorState.vue'
import IssueList from '@/components/ui/IssueList.vue'
import SimulatedBadge from '@/components/ui/SimulatedBadge.vue'
import Skeleton from '@/components/ui/Skeleton.vue'
import ViewToggle from '@/components/ui/ViewToggle.vue'
import { formatPercent, formatTimestamp, shortId } from '@/composables/format'
import { jobProgress, useJob } from '@/composables/useJob'
import { setPageDetail } from '@/composables/usePageMeta'
import { param, useQueryState } from '@/composables/useQueryState'
import { useResource } from '@/composables/useResource'

/*
 * Two evaluations side by side. `?a=&b=` names two suites; the comparison is computed by the
 * API as an experiment (a job), whose id is then kept in `?experiment=` so reloading the page
 * does not compute it again. An experiment can also run both sides now from a dataset and
 * two agent versions.
 */
const { state: query, set } = useQueryState({
  a: param.string(),
  b: param.string(),
  experiment: param.string(),
  case: param.string(),
  checks: param.enum(['chart', 'table'] as const),
})

const comparison = useResource(
  (signal) => (query.value.experiment ? api.getExperiment(query.value.experiment, signal) : Promise.resolve(null)),
  () => query.value.experiment,
)
const suites = useResource((signal) => api.listEvaluations(signal))
const experiments = useResource((signal) => api.listExperiments(signal))
const datasets = useResource((signal) => api.listDatasets(signal))
const catalog = useResource((signal) => api.agents(signal))
const creating = useJob()
// Comparing two finished suites is synchronous: no job to poll.
const comparing = ref(false)
const compareError = shallowRef<unknown>(null)

const completed = computed(() => (suites.data.value ?? []).filter((s) => s.status === 'completed'))
const suiteById = (id: string | null) => completed.value.find((s) => s.suite_id === id) ?? null
const suiteLabel = (s: SuiteListItem) =>
  `${s.agent_ref} · ${s.dataset_name}@${s.dataset_version} · ${s.passed}/${s.total} · ${shortId(s.suite_id)}`
const agentRefs = computed(() => catalog.data.value?.agents.flatMap((a) => a.versions.map((v) => `${v.agent}@${v.version}`)) ?? [])

// Two suites named in the URL without a stored comparison: compute it once (after the suite
// list has loaded, so the comparison is named after the agent versions).
watch(
  () => [query.value.a, query.value.b, query.value.experiment, suites.loading.value] as const,
  async ([a, b, experiment, loadingSuites]) => {
    if (experiment || !a || !b || loadingSuites || creating.running.value || comparing.value) return
    const name = `${suiteById(a)?.agent_ref ?? shortId(a)} vs ${suiteById(b)?.agent_ref ?? shortId(b)}`
    comparing.value = true
    compareError.value = null
    try {
      const stored = await api.compareSuites({ name, suite_a: a, suite_b: b })
      await set({ experiment: stored.experiment_id })
    } catch (e) {
      compareError.value = e
    } finally {
      comparing.value = false
    }
  },
  { immediate: true },
)

const pickA = ref(query.value.a ?? '')
const pickB = ref(query.value.b ?? '')
const mismatch = computed(() => {
  const [a, b] = [suiteById(pickA.value), suiteById(pickB.value)]
  return Boolean(a && b && a.dataset_hash !== b.dataset_hash)
})
function compareSuites(): void {
  void set({ a: pickA.value, b: pickB.value, experiment: null, case: null }, { push: true })
}

const runDataset = ref('')
const runA = ref('')
const runB = ref('')
async function runExperiment(): Promise<void> {
  const at = runDataset.value.lastIndexOf('@')
  if (at <= 0 || !runA.value || !runB.value) return
  const done = await creating.start(() =>
    api.createExperiment({
      name: `${runA.value} vs ${runB.value}`,
      dataset: { name: runDataset.value.slice(0, at), version: runDataset.value.slice(at + 1) },
      a: { agent_ref: runA.value },
      b: { agent_ref: runB.value },
    }),
  )
  if (done) await set({ experiment: done.resource_id, a: null, b: null, case: null }, { push: true })
}

function changeSelection(): void {
  void set({ experiment: null, a: null, b: null, case: null }, { push: true })
}

const data = computed(() => comparison.data.value)
const metricRows = computed(() => (data.value?.metric_deltas ?? []).filter((d) => !d.metric.startsWith(CHECK_PREFIX)))
const checkRows = computed(() => (data.value?.metric_deltas ?? []).filter((d) => d.metric.startsWith(CHECK_PREFIX)))
const selected = computed(() => data.value?.cases.find((c) => c.case_id === query.value.case) ?? null)
const diffRuns = useResource(
  async (signal) => {
    const c = selected.value
    if (!c?.a_run_id || !c.b_run_id) return null
    const [a, b] = await Promise.all([api.getRun(c.a_run_id, signal), api.getRun(c.b_run_id, signal)])
    return { a, b }
  },
  () => `${selected.value?.a_run_id}|${selected.value?.b_run_id}`,
)
const creatingError = computed(() => {
  const failure = creating.error.value ?? compareError.value
  return failure ? describeError(failure) : null
})
const showPicker = computed(() => !query.value.experiment && !creating.running.value && !comparing.value)

watch(data, (value) => setPageDetail(value ? value.name : null), { immediate: true })
</script>

<template>
  <div class="screen">
    <header class="screen-head">
      <div>
        <h1 tabindex="-1">{{ data?.name ?? 'Compare' }}</h1>
        <p v-if="data" class="subtitle">
          A <RouterLink :to="`/evaluations/${encodeURIComponent(data.a.suite_id)}`" class="mono">{{ data.a.agent_ref }}</RouterLink>
          → B <RouterLink :to="`/evaluations/${encodeURIComponent(data.b.suite_id)}`" class="mono">{{ data.b.agent_ref }}</RouterLink>
          · {{ formatTimestamp(data.created_at) }}
        </p>
        <p v-else class="subtitle">Pick two evaluations of the same dataset, or run two agent versions on one now.</p>
      </div>
      <div class="actions">
        <SimulatedBadge v-if="data?.deterministic" />
        <button v-if="query.experiment" type="button" class="btn" @click="changeSelection">Change selection</button>
      </div>
    </header>

    <p v-if="creating.running.value || comparing" class="callout info" role="status">
      Computing the comparison… {{ comparing ? '' : jobProgress(creating.job.value) }}
    </p>
    <div v-if="creatingError" class="callout fail" role="alert">
      <div>
        Could not compare: {{ creatingError.message }}
        <IssueList v-if="creatingError.issues.length" :issues="creatingError.issues" />
      </div>
    </div>

    <template v-if="showPicker">
      <div class="pickers">
        <section class="picker panel" aria-labelledby="pick-suites-title">
          <h2 id="pick-suites-title" class="section-title">Compare two evaluations</h2>
          <template v-if="completed.length >= 2">
            <label class="field">
              <span>A (baseline)</span>
              <select v-model="pickA" class="select mono-select">
                <option value="" disabled>Choose an evaluation</option>
                <option v-for="s in completed" :key="s.suite_id" :value="s.suite_id">{{ suiteLabel(s) }}</option>
              </select>
            </label>
            <label class="field">
              <span>B (candidate)</span>
              <select v-model="pickB" class="select mono-select">
                <option value="" disabled>Choose an evaluation</option>
                <option v-for="s in completed" :key="s.suite_id" :value="s.suite_id">{{ suiteLabel(s) }}</option>
              </select>
            </label>
            <p v-if="mismatch" class="callout warn" role="note">
              These evaluations ran different dataset content: only shared, unchanged cases will be compared.
            </p>
            <button type="button" class="btn primary" :disabled="!pickA || !pickB || pickA === pickB" @click="compareSuites">
              Compare
            </button>
          </template>
          <ErrorState v-else-if="suites.error.value" :error="suites.error.value" title="Could not load the evaluations" compact @retry="suites.reload" />
          <Skeleton v-else-if="!suites.data.value" :rows="3" />
          <div v-else class="example">
            <p>Comparing needs two finished evaluations of the same dataset. For example:</p>
            <pre class="mono">agentscope eval --dataset research-assistant --agent research_assistant@v1
agentscope eval --dataset research-assistant --agent research_assistant@v2</pre>
            <p>Or load the demo from the Overview, or run an experiment here.</p>
          </div>
        </section>

        <section class="picker panel" aria-labelledby="run-experiment-title">
          <h2 id="run-experiment-title" class="section-title">Run an experiment</h2>
          <label class="field">
            <span>Dataset version</span>
            <select v-model="runDataset" class="select mono-select">
              <option value="" disabled>Choose a dataset</option>
              <option v-for="d in datasets.data.value ?? []" :key="`${d.name}@${d.version}`" :value="`${d.name}@${d.version}`">
                {{ d.name }}@{{ d.version }}
              </option>
            </select>
          </label>
          <label class="field">
            <span>Agent version A</span>
            <select v-model="runA" class="select mono-select">
              <option value="" disabled>Choose an agent version</option>
              <option v-for="r in agentRefs" :key="r" :value="r">{{ r }}</option>
            </select>
          </label>
          <label class="field">
            <span>Agent version B</span>
            <select v-model="runB" class="select mono-select">
              <option value="" disabled>Choose an agent version</option>
              <option v-for="r in agentRefs" :key="r" :value="r">{{ r }}</option>
            </select>
          </label>
          <button type="button" class="btn primary" :disabled="!runDataset || !runA || !runB" @click="runExperiment">
            Run both and compare
          </button>
        </section>
      </div>

      <section v-if="experiments.data.value?.length" class="section" aria-labelledby="recent-title">
        <h2 id="recent-title" class="section-title">Recent comparisons</h2>
        <div class="table-wrap panel">
          <table class="data-table">
            <thead>
              <tr>
                <th scope="col">Comparison</th>
                <th scope="col">Dataset</th>
                <th scope="col" class="num">A</th>
                <th scope="col" class="num">B</th>
                <th scope="col" class="num">Regressions</th>
                <th scope="col" class="num">Fixes</th>
                <th scope="col">Created</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="x in experiments.data.value" :key="x.experiment_id">
                <th scope="row">
                  <RouterLink :to="{ path: '/compare', query: { experiment: x.experiment_id } }">{{ x.name }}</RouterLink>
                  <span v-if="x.confounded" class="tag">confounded</span>
                </th>
                <td class="mono">{{ x.dataset_name }}@{{ x.dataset_version }}</td>
                <td class="num mono">{{ x.a_ref }} {{ formatPercent(x.a_pass_rate) }}</td>
                <td class="num mono">{{ x.b_ref }} {{ formatPercent(x.b_pass_rate) }}</td>
                <td class="num mono">{{ x.regressions }}</td>
                <td class="num mono">{{ x.fixes }}</td>
                <td class="faint">{{ formatTimestamp(x.created_at) }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </section>
    </template>

    <template v-else-if="query.experiment">
      <ErrorState v-if="comparison.error.value && !data" :error="comparison.error.value" title="Could not load this comparison" @retry="comparison.reload" />
      <Skeleton v-else-if="!data" :rows="10" />
      <template v-else>
        <dl class="facts">
          <div>
            <dt>Dataset</dt>
            <dd class="mono">{{ data.dataset_name }}@{{ data.dataset_version }}</dd>
          </div>
          <div>
            <dt>Dataset hash</dt>
            <dd class="mono">{{ data.dataset_hash }} <CopyButton :value="data.dataset_hash" label="Copy the dataset hash" /></dd>
          </div>
        </dl>
        <p v-if="data.dataset_mismatch" class="callout warn" role="note">
          <span>
            <strong>Dataset content differs.</strong> A ran {{ shortId(data.a.dataset_hash.replace('sha256:', ''), 12) }}, B ran
            {{ shortId(data.b.dataset_hash.replace('sha256:', ''), 12) }}: only shared, unchanged cases are compared.
          </span>
        </p>
        <p v-if="!data.deterministic" class="callout warn" role="note">
          At least one side used a non-deterministic model: flips may be noise. Re-run to confirm before trusting them.
        </p>

        <ConfigDiff :diff="data.config_diff" :confounded="data.confounded" />

        <section class="section" aria-labelledby="deltas-title">
          <h2 id="deltas-title" class="section-title">Metrics</h2>
          <MetricDeltaTable :deltas="metricRows" :deterministic="data.deterministic" />
        </section>

        <section v-if="checkRows.length" class="section" aria-labelledby="checks-title">
          <div class="section-head">
            <h2 id="checks-title" class="section-title">Pass rate per check</h2>
            <div class="actions">
              <ViewToggle
                :model-value="query.checks ?? 'chart'"
                label="Show the per-check pass rate as"
                @update:model-value="set({ checks: $event === 'chart' ? null : $event })"
              />
            </div>
          </div>
          <CheckRateBars :deltas="checkRows" :flips="data.check_flips" :view="query.checks ?? 'chart'" />
        </section>

        <section class="section" aria-labelledby="flips-title">
          <h2 id="flips-title" class="section-title">Cases</h2>
          <FlipList :cases="data.cases" :selected="query.case" @select="set({ case: $event }, { push: true })" />
        </section>

        <section v-if="selected" id="trace-diff" class="section" aria-labelledby="diff-title">
          <div class="section-head">
            <h2 id="diff-title" class="section-title">Trace diff · {{ selected.case_id }}</h2>
            <div class="actions">
              <RouterLink
                v-if="selected.a_run_id && selected.b_run_id"
                :to="{ path: '/compare/runs', query: { a: selected.a_run_id, b: selected.b_run_id } }"
                class="btn ghost"
              >
                Open on its own page
              </RouterLink>
            </div>
          </div>
          <p v-if="!selected.a_run_id || !selected.b_run_id" class="callout info" role="note">
            There is no trace diff for this case: {{ !selected.a_run_id && !selected.b_run_id ? 'neither side has' : `side ${selected.a_run_id ? 'B' : 'A'} has no` }}
            recorded run (the case was not run there, the harness could not run it, or the run was purged).
          </p>
          <ErrorState v-else-if="diffRuns.error.value && !diffRuns.data.value" :error="diffRuns.error.value" title="Could not load the two runs" compact @retry="diffRuns.reload" />
          <Skeleton v-else-if="!diffRuns.data.value" :rows="6" />
          <TraceDiff
            v-else
            :a="diffRuns.data.value.a"
            :b="diffRuns.data.value.b"
            :label-a="`A ${data.a.agent_ref}`"
            :label-b="`B ${data.b.agent_ref}`"
          />
        </section>
      </template>
    </template>
  </div>
</template>

<style scoped>
.pickers {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 18px;
  align-items: start;
}
.picker {
  display: grid;
  gap: 10px;
  padding: 12px 14px;
}
.picker .btn {
  justify-self: start;
}
.mono-select {
  font-family: var(--font-mono);
  font-size: 12px;
}
.example p {
  margin: 0 0 6px;
  font-size: 13px;
  color: var(--text-muted);
}
.example pre {
  margin: 0 0 6px;
  padding: 8px 10px;
  border-radius: var(--radius);
  background: var(--surface-2);
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
.facts {
  display: flex;
  flex-wrap: wrap;
  gap: 8px 28px;
  margin: 0;
}
.facts dt {
  font-size: 11px;
  color: var(--text-muted);
}
.facts dd {
  display: flex;
  align-items: center;
  gap: 4px;
  margin: 1px 0 0;
  font-size: 13px;
}
.tag {
  margin-left: 6px;
}
</style>
