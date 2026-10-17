<script setup lang="ts">
import { computed, watch } from 'vue'
import { api, MAX_PAGE } from '@/api/client'
import FailureBuckets from '@/components/failures/FailureBuckets.vue'
import InjectionSummary from '@/components/failures/InjectionSummary.vue'
import ResilienceTable from '@/components/failures/ResilienceTable.vue'
import EmptyState from '@/components/ui/EmptyState.vue'
import ErrorState from '@/components/ui/ErrorState.vue'
import SimulatedBadge from '@/components/ui/SimulatedBadge.vue'
import Skeleton from '@/components/ui/Skeleton.vue'
import { plural, shortId } from '@/composables/format'
import { param, useQueryState } from '@/composables/useQueryState'
import { useResource } from '@/composables/useResource'

const { state: query, set } = useQueryState({ agent: param.string(), suite: param.string() })
const analysis = useResource(
  (signal) => api.failures({ agent_ref: query.value.agent ?? undefined, suite_id: query.value.suite ?? undefined }, signal),
  () => `${query.value.agent}|${query.value.suite}`,
)
// The evaluation filter lists the chosen agent version's suites (the API pages; it is asked
// for its maximum), so older suites stay reachable by choosing their agent version first.
const suites = useResource((signal) =>
  api.listEvaluations({ agent_ref: query.value.agent ?? undefined, limit: MAX_PAGE }, signal),
)
watch(
  () => query.value.agent,
  () => void suites.reload(),
)
const suiteList = computed(() => suites.data.value?.items ?? [])
const suitesTotal = computed(() => suites.data.value?.total ?? null)

const data = computed(() => analysis.data.value)
const filtered = computed(() => Boolean(query.value.agent || query.value.suite))
const byAgent = <T extends { agent_ref: string }>(rows: T[]) =>
  rows.filter((r) => !query.value.agent || r.agent_ref === query.value.agent)
const resilience = computed(() => byAgent(data.value?.resilience ?? []))
const injection = computed(() => byAgent(data.value?.injection ?? []))
const buckets = computed(() => data.value?.buckets ?? [])

// A server that ignores a filter returns more than was asked for; say what the numbers cover.
const agentIgnored = computed(() => {
  const agent = query.value.agent
  const d = data.value
  return Boolean(agent && d && [...d.resilience, ...d.injection].some((r) => r.agent_ref !== agent))
})
const suiteIgnored = computed(() => {
  const suite = query.value.suite
  return Boolean(suite && data.value?.suites_considered.some((id) => id !== suite))
})

const agentOptions = computed(() => {
  const refs = new Set([
    ...suiteList.value.map((s) => s.agent_ref),
    ...(data.value?.resilience ?? []).map((r) => r.agent_ref),
    ...(data.value?.injection ?? []).map((r) => r.agent_ref),
  ])
  if (query.value.agent) refs.add(query.value.agent)
  return [...refs].sort()
})
const suiteOptions = computed(() =>
  suiteList.value.filter((s) => !query.value.agent || s.agent_ref === query.value.agent),
)
/** A chosen suite missing from the list (older, or purged) stays selectable by its id. */
const unlistedSuite = computed(() => {
  const id = query.value.suite
  return id && !suiteOptions.value.some((s) => s.suite_id === id) ? id : null
})

/*
 * Which numbers come from the simulated model (the fake provider). Per agent version: its
 * considered suites as listed, and the server's own flag on the injection summary. The whole
 * page is labelled when every considered suite is known to be simulated.
 */
const listedById = computed(() => new Map(suiteList.value.map((s) => [s.suite_id, s])))
const simulatedAgents = computed(() => {
  const out = new Set<string>()
  for (const id of data.value?.suites_considered ?? []) {
    const suite = listedById.value.get(id)
    if (suite?.deterministic) out.add(suite.agent_ref)
  }
  for (const item of data.value?.injection ?? []) if (item.deterministic) out.add(item.agent_ref)
  return out
})
const allSimulated = computed(() => {
  const considered = data.value?.suites_considered ?? []
  return considered.length > 0 && considered.every((id) => listedById.value.get(id)?.deterministic === true)
})
const simulatedNote = computed(() =>
  !allSimulated.value && simulatedAgents.value.size ? [...simulatedAgents.value].sort().join(', ') : null,
)
const nothing = computed(() => !resilience.value.length && !buckets.value.length && !injection.value.length)

function onFilter(key: 'agent' | 'suite', event: Event): void {
  void set({ [key]: (event.target as HTMLSelectElement).value || null })
}
</script>

<template>
  <div class="screen">
    <header class="screen-head">
      <div>
        <h1 tabindex="-1">Failures</h1>
        <p class="subtitle">
          How agent versions handle injected faults and prompt injection, and where their failed checks cluster.
          <template v-if="data">Across {{ plural(data.suites_considered.length, 'evaluation') }}.</template>
        </p>
      </div>
      <div v-if="data && allSimulated" class="actions"><SimulatedBadge /></div>
    </header>

    <form class="filters" aria-label="Filter failures" @submit.prevent>
      <label class="field">
        <span>Agent version</span>
        <select class="select mono-select" :value="query.agent ?? ''" @change="onFilter('agent', $event)">
          <option value="">All agent versions</option>
          <option v-for="a in agentOptions" :key="a" :value="a">{{ a }}</option>
        </select>
      </label>
      <label class="field">
        <span>Evaluation</span>
        <select class="select mono-select" :value="query.suite ?? ''" @change="onFilter('suite', $event)">
          <option value="">All evaluations</option>
          <option v-if="unlistedSuite" :value="unlistedSuite">Evaluation {{ shortId(unlistedSuite) }}</option>
          <option v-for="s in suiteOptions" :key="s.suite_id" :value="s.suite_id">
            {{ s.label || s.agent_ref }} · {{ s.dataset_name }}@{{ s.dataset_version }} · {{ shortId(s.suite_id) }}
          </option>
        </select>
      </label>
      <button v-if="filtered" type="button" class="btn ghost" @click="set({ agent: null, suite: null })">Clear filters</button>
      <span v-if="suitesTotal !== null && suitesTotal > suiteList.length" class="faint cut">
        Listing the newest {{ suiteList.length }} of {{ suitesTotal }} evaluations{{ query.agent ? '' : '; choose an agent version to narrow it' }}.
      </span>
    </form>

    <ErrorState v-if="analysis.error.value && !data" :error="analysis.error.value" title="Could not load the failure analysis" @retry="analysis.reload" />
    <Skeleton v-else-if="!data" :rows="8" />
    <EmptyState v-else-if="!data.suites_considered.length && !filtered" title="No evaluations to analyse yet" variant="first-run">
      <p>Failure analysis aggregates finished evaluations. Evaluate a dataset with faults, or load the demo from the Overview.</p>
    </EmptyState>
    <EmptyState v-else-if="nothing" title="No failures recorded for these filters" variant="filtered">
      <template #actions>
        <button type="button" class="btn" @click="set({ agent: null, suite: null })">Clear filters</button>
      </template>
    </EmptyState>

    <template v-else>
      <p v-if="suiteIgnored" class="callout warn" role="note">
        The server did not apply the evaluation filter: these numbers cover {{ plural(data.suites_considered.length, 'evaluation') }}.
      </p>
      <p v-if="agentIgnored" class="callout warn" role="note">
        The server did not apply the agent filter. The resilience and injection tables are filtered here; the failure
        buckets still cover every agent version.
      </p>

      <section class="section" aria-labelledby="resilience-title">
        <h2 id="resilience-title" class="section-title">Resilience by fault kind</h2>
        <ResilienceTable v-if="resilience.length" :rows="resilience" :simulated="simulatedAgents" />
        <p v-else class="faint none">No case with an injected fault was evaluated.</p>
      </section>

      <section class="section" aria-labelledby="injection-title">
        <h2 id="injection-title" class="section-title">Prompt injection</h2>
        <InjectionSummary v-if="injection.length" :items="injection" />
        <p v-else class="faint none">No prompt-injection case was evaluated.</p>
      </section>

      <section class="section" aria-labelledby="buckets-title">
        <h2 id="buckets-title" class="section-title">Failure buckets</h2>
        <p v-if="simulatedNote && buckets.length" class="faint note">
          Includes evaluations of a simulated model ({{ simulatedNote }}): those failures demonstrate harness
          mechanics, not model behaviour.
        </p>
        <FailureBuckets v-if="buckets.length" :buckets="buckets" />
        <p v-else class="faint none">No check failed.</p>
      </section>
    </template>
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
.none {
  margin: 0;
  font-size: 13px;
}
.cut {
  padding-bottom: 5px;
  font-size: 12.5px;
}
.note {
  margin: 0;
  font-size: 12.5px;
}
</style>
