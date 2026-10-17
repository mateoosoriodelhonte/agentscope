<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, shallowRef, watch } from 'vue'
import { RouterLink } from 'vue-router'
import { api, pollJob, type RunListQuery, type Verdict } from '@/api/client'
import { RUN_STATUSES, type RunSummary } from '@/api/types'
import CopyButton from '@/components/ui/CopyButton.vue'
import EmptyState from '@/components/ui/EmptyState.vue'
import SimulatedBadge from '@/components/ui/SimulatedBadge.vue'
import Skeleton from '@/components/ui/Skeleton.vue'
import StatusBadge from '@/components/ui/StatusBadge.vue'
import { formatDuration, formatTimestamp, plural, shortId } from '@/composables/format'
import { openNewRun } from '@/composables/newRun'
import { param, useQueryState } from '@/composables/useQueryState'
import { SIMULATED_PROVIDER } from '@/composables/useTraceModel'

const PAGE_SIZE = 50
const VERDICTS = ['passed', 'failed', 'none'] as const

const { state: filters, set } = useQueryState({
  status: param.enum(RUN_STATUSES),
  agent: param.string(),
  faults: param.boolean(),
  blocked: param.boolean(),
  verdict: param.enum(VERDICTS),
  page: param.int(1, 1),
})

const runs = shallowRef<RunSummary[]>([])
const total = ref(0)
const dropped = ref(0)
const error = ref<string | null>(null)
const loading = ref(true)
const agentRefs = shallowRef<string[]>([])
const seeding = ref<string | null>(null)
let controller: AbortController | null = null
let seedController: AbortController | null = null

const filtered = computed(() => {
  const f = filters.value
  return Boolean(f.status || f.agent || f.faults !== null || f.blocked !== null || f.verdict)
})

function verdictOf(run: RunSummary): Verdict {
  return run.passed === null ? 'none' : run.passed ? 'passed' : 'failed'
}

/** Guards against a server that ignores a filter: rows shown always match the filters. */
function matches(run: RunSummary): boolean {
  const f = filters.value
  return (
    (!f.status || run.status === f.status) &&
    (!f.agent || `${run.agent}@${run.agent_version}` === f.agent) &&
    (f.faults === null || run.faults > 0 === f.faults) &&
    (f.blocked === null || run.blocked_tool_calls > 0 === f.blocked) &&
    (!f.verdict || verdictOf(run) === f.verdict)
  )
}

async function load(): Promise<void> {
  controller?.abort()
  const current = new AbortController()
  controller = current
  const f = filters.value
  const [agent, agentVersion] = f.agent?.split('@') ?? []
  const query: RunListQuery = {
    status: f.status ?? undefined,
    agent,
    agent_version: agentVersion,
    has_faults: f.faults ?? undefined,
    has_blocked_calls: f.blocked ?? undefined,
    verdict: f.verdict ?? undefined,
    limit: PAGE_SIZE,
    offset: (f.page - 1) * PAGE_SIZE,
  }
  loading.value = true
  try {
    const result = await api.listRuns(query, current.signal)
    runs.value = result.items.filter(matches)
    dropped.value = result.items.length - runs.value.length
    total.value = result.total
    error.value = null
  } catch (e) {
    if (current.signal.aborted) return
    error.value = e instanceof Error ? e.message : String(e)
  } finally {
    if (!current.signal.aborted) loading.value = false
  }
}

watch(filters, load, { immediate: true })
onBeforeUnmount(() => {
  controller?.abort()
  seedController?.abort()
})

onMounted(async () => {
  try {
    const { agents } = await api.agents()
    agentRefs.value = agents.flatMap((a) => a.versions.map((v) => `${v.agent}@${v.version}`))
  } catch {
    /* the filter falls back to the agents seen in the list */
  }
})

const agentOptions = computed(() => {
  const seen = new Set(agentRefs.value)
  for (const run of runs.value) seen.add(`${run.agent}@${run.agent_version}`)
  if (filters.value.agent) seen.add(filters.value.agent)
  return [...seen].sort()
})

const lastPage = computed(() => Math.max(1, Math.ceil(total.value / PAGE_SIZE)))
/** Positions in the server's result set (before any rows the client had to hide). */
const range = computed(() => {
  const start = (filters.value.page - 1) * PAGE_SIZE
  const count = runs.value.length + dropped.value
  return count ? `${start + 1}–${start + count} of ${total.value}` : `page ${filters.value.page} of ${lastPage.value}`
})
// When the server ignored a filter its total counts runs that do not match, so say so.
const countLabel = computed(() =>
  dropped.value ? `${plural(total.value, 'run')} before client-side filtering` : plural(total.value, 'run'),
)

function update(patch: Parameters<typeof set>[0]): void {
  void set({ ...patch, page: 1 })
}

function onSelect(key: 'status' | 'agent' | 'verdict', event: Event): void {
  const value = (event.target as HTMLSelectElement).value || null
  update({ [key]: value })
}

function onTriState(key: 'faults' | 'blocked', event: Event): void {
  const value = (event.target as HTMLSelectElement).value
  update({ [key]: value === '' ? null : value === 'yes' })
}

function clearFilters(): void {
  void set({ status: null, agent: null, faults: null, blocked: null, verdict: null, page: 1 })
}

async function loadDemo(): Promise<void> {
  seedController?.abort()
  const current = new AbortController()
  seedController = current
  seeding.value = 'Seeding demo data…'
  try {
    const job = await api.seedDemo()
    await pollJob(job.job_id, {
      signal: current.signal,
      onProgress: (status) => {
        if (status.total) seeding.value = `Seeding demo data… ${status.progress}/${status.total}`
      },
    })
    seeding.value = null
    await load()
  } catch (e) {
    if (current.signal.aborted) return
    seeding.value = `Could not load the demo: ${e instanceof Error ? e.message : String(e)}`
  }
}

const triState = (value: boolean | null) => (value === null ? '' : value ? 'yes' : 'no')
</script>

<template>
  <div class="page">
    <header class="head">
      <h1 tabindex="-1">Runs</h1>
      <span v-if="!error && !loading" class="count muted">{{ countLabel }}</span>
      <button type="button" class="btn primary new-run" @click="openNewRun()">New run</button>
    </header>

    <form class="filters" aria-label="Filter runs" @submit.prevent>
      <label>
        <span>Status</span>
        <select class="select" :value="filters.status ?? ''" @change="onSelect('status', $event)">
          <option value="">Any</option>
          <option v-for="s in RUN_STATUSES" :key="s" :value="s">{{ s.replace('_', ' ') }}</option>
        </select>
      </label>
      <label>
        <span>Agent</span>
        <select class="select mono-select" :value="filters.agent ?? ''" @change="onSelect('agent', $event)">
          <option value="">Any</option>
          <option v-for="agentRef in agentOptions" :key="agentRef" :value="agentRef">{{ agentRef }}</option>
        </select>
      </label>
      <label>
        <span>Faults injected</span>
        <select class="select" :value="triState(filters.faults)" @change="onTriState('faults', $event)">
          <option value="">Any</option>
          <option value="yes">With faults</option>
          <option value="no">Without faults</option>
        </select>
      </label>
      <label>
        <span>Blocked calls</span>
        <select class="select" :value="triState(filters.blocked)" @change="onTriState('blocked', $event)">
          <option value="">Any</option>
          <option value="yes">With blocked calls</option>
          <option value="no">Without blocked calls</option>
        </select>
      </label>
      <label>
        <span>Verdict</span>
        <select class="select" :value="filters.verdict ?? ''" @change="onSelect('verdict', $event)">
          <option value="">Any</option>
          <option value="passed">Passed</option>
          <option value="failed">Failed</option>
          <option value="none">Not evaluated</option>
        </select>
      </label>
      <button v-if="filtered" type="button" class="btn ghost" @click="clearFilters">Clear filters</button>
    </form>

    <EmptyState v-if="error" title="Could not load runs" variant="error">
      <p>{{ error }}</p>
      <p>Is the API running? Start it with <code>agentscope serve</code>.</p>
      <template #actions>
        <button type="button" class="btn" @click="load">Retry</button>
      </template>
    </EmptyState>

    <Skeleton v-else-if="loading && runs.length === 0" :rows="8" />

    <!-- Empty states only when the server found nothing at all: an empty page of a non-empty
         result (past the last page, or every row hidden by a filter the server ignored) is not
         "no runs". -->
    <EmptyState v-else-if="total === 0 && filtered" title="No runs match these filters" variant="filtered">
      <p>Try removing a filter.</p>
      <template #actions>
        <button type="button" class="btn" @click="clearFilters">Clear filters</button>
      </template>
    </EmptyState>

    <EmptyState v-else-if="total === 0" title="No runs yet" variant="first-run">
      <p>Run an agent from the command line, then come back here to explore its trace:</p>
      <p>
        <code>agentscope run --agent research_assistant@v2 --task "In what year did the Meridian Footbridge open?" --expect 1987</code>
      </p>
      <p v-if="seeding" role="status">{{ seeding }}</p>
      <template #actions>
        <button type="button" class="btn primary" @click="loadDemo">Load demo data</button>
      </template>
    </EmptyState>

    <template v-else>
      <p v-if="dropped" class="notice" role="note">
        The server did not apply every filter; {{ plural(dropped, 'non-matching run') }} on this page
        {{ dropped === 1 ? 'is' : 'are' }} hidden.
      </p>
      <p v-if="runs.length === 0" class="notice empty-page" role="status">
        <template v-if="dropped">No run on this page matches every filter; other pages may.</template>
        <template v-else>No runs on this page.</template>
        <button v-if="filters.page > 1" type="button" class="btn ghost" @click="set({ page: 1 })">First page</button>
      </p>
      <div v-else class="table-wrap panel" :aria-busy="loading">
        <table class="runs">
          <thead>
            <tr>
              <th scope="col">Status</th>
              <th scope="col">Task</th>
              <th scope="col">Agent · model</th>
              <th scope="col" class="num">Steps</th>
              <th scope="col" class="num">Tool calls</th>
              <th scope="col" class="num">Faults</th>
              <th scope="col">Verdict</th>
              <th scope="col" class="num">Duration</th>
              <th scope="col">Started</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="run in runs" :key="run.run_id">
              <td><StatusBadge :status="run.status" /></td>
              <td class="task">
                <RouterLink :to="`/runs/${encodeURIComponent(run.run_id)}`" class="task-link">{{ run.task }}</RouterLink>
                <!-- The full id is what Compare runs and the API take: shown on hover, copied here. -->
                <span class="run-id mono" :title="run.run_id">
                  {{ shortId(run.run_id) }}
                  <CopyButton :value="run.run_id" :label="`Copy run id ${shortId(run.run_id)}`" />
                </span>
              </td>
              <td>
                <span class="mono agent">{{ run.agent }}@{{ run.agent_version }}</span>
                <span class="model">
                  <span class="mono">{{ run.model }}</span>
                  <SimulatedBadge v-if="run.provider === SIMULATED_PROVIDER" compact />
                  <span v-else class="faint">{{ run.provider }}</span>
                </span>
              </td>
              <td class="num mono">{{ run.steps }}</td>
              <td class="num mono">
                {{ run.tool_calls }}
                <span v-if="run.blocked_tool_calls" class="blocked">{{ run.blocked_tool_calls }} blocked</span>
              </td>
              <td class="num mono">
                <span v-if="run.faults" class="faults">{{ run.faults }}</span>
                <span v-else class="faint">—</span>
              </td>
              <td>
                <StatusBadge v-if="run.passed !== null" :status="run.passed ? 'passed' : 'failed'" />
                <span v-else class="faint">not evaluated</span>
              </td>
              <td class="num mono">{{ formatDuration(run.duration_ms) }}</td>
              <td class="started">{{ formatTimestamp(run.started_at) }}</td>
            </tr>
          </tbody>
        </table>
      </div>
      <nav class="pager" aria-label="Pagination">
        <span class="muted">{{ range }}</span>
        <button type="button" class="btn" :disabled="filters.page <= 1" @click="set({ page: filters.page - 1 })">
          Previous
        </button>
        <button type="button" class="btn" :disabled="filters.page >= lastPage" @click="set({ page: filters.page + 1 })">
          Next
        </button>
      </nav>
    </template>
  </div>
</template>

<style scoped>
.page {
  padding: 18px 24px 32px;
}
.head {
  display: flex;
  align-items: baseline;
  gap: 10px;
  margin-bottom: 14px;
}
.count {
  font-size: 13px;
}
.new-run {
  margin-left: auto;
}
.filters {
  display: flex;
  flex-wrap: wrap;
  align-items: flex-end;
  gap: 12px;
  margin-bottom: 14px;
}
.filters label {
  display: flex;
  flex-direction: column;
  gap: 3px;
  font-size: 11.5px;
  color: var(--text-muted);
}
.mono-select {
  font-family: var(--font-mono);
  font-size: 12px;
}
.table-wrap {
  overflow-x: auto;
}
.runs {
  width: 100%;
  border-collapse: collapse;
  font-size: 13px;
}
th,
td {
  padding: 7px 10px;
  border-bottom: 1px solid var(--border);
  text-align: left;
  vertical-align: middle;
  white-space: nowrap;
}
th {
  position: sticky;
  top: 0;
  background: var(--surface-2);
  font-size: 11.5px;
  font-weight: 500;
  color: var(--text-muted);
}
th.num,
td.num {
  text-align: right;
}
tbody tr:hover {
  background: var(--hover);
}
tbody tr:last-child td {
  border-bottom: 0;
}
.task {
  width: 40%;
  max-width: 0;
}
.task-link {
  display: block;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--text);
  font-weight: 500;
}
.task-link:hover {
  color: var(--accent);
}
.run-id {
  display: inline-flex;
  align-items: center;
  gap: 2px;
  font-size: 11px;
  color: var(--text-faint);
}
.agent {
  display: block;
}
.model {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 11px;
  color: var(--text-muted);
}
.model .mono {
  font-size: 11px;
}
.blocked {
  display: block;
  font-size: 11px;
  color: var(--warn);
}
.faults {
  color: var(--injected);
}
.started {
  color: var(--text-muted);
  font-size: 12px;
}
.notice {
  margin: 0 0 10px;
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
  margin-top: 10px;
  font-size: 12.5px;
}
</style>
