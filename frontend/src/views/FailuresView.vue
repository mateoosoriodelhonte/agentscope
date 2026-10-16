<script setup lang="ts">
import { computed } from 'vue'
import { api } from '@/api/client'
import FailureBuckets from '@/components/failures/FailureBuckets.vue'
import InjectionSummary from '@/components/failures/InjectionSummary.vue'
import ResilienceTable from '@/components/failures/ResilienceTable.vue'
import EmptyState from '@/components/ui/EmptyState.vue'
import ErrorState from '@/components/ui/ErrorState.vue'
import Skeleton from '@/components/ui/Skeleton.vue'
import { plural, shortId } from '@/composables/format'
import { param, useQueryState } from '@/composables/useQueryState'
import { useResource } from '@/composables/useResource'

const { state: query, set } = useQueryState({ agent: param.string(), suite: param.string() })
const analysis = useResource(
  (signal) => api.failures({ agent_ref: query.value.agent ?? undefined, suite_id: query.value.suite ?? undefined }, signal),
  () => `${query.value.agent}|${query.value.suite}`,
)
const suites = useResource((signal) => api.listEvaluations(signal))

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
    ...(suites.data.value ?? []).map((s) => s.agent_ref),
    ...(data.value?.resilience ?? []).map((r) => r.agent_ref),
    ...(data.value?.injection ?? []).map((r) => r.agent_ref),
  ])
  if (query.value.agent) refs.add(query.value.agent)
  return [...refs].sort()
})
const suiteOptions = computed(() =>
  (suites.data.value ?? []).filter((s) => !query.value.agent || s.agent_ref === query.value.agent),
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
          <option v-for="s in suiteOptions" :key="s.suite_id" :value="s.suite_id">
            {{ s.label || s.agent_ref }} · {{ s.dataset_name }}@{{ s.dataset_version }} · {{ shortId(s.suite_id) }}
          </option>
        </select>
      </label>
      <button v-if="filtered" type="button" class="btn ghost" @click="set({ agent: null, suite: null })">Clear filters</button>
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
        <ResilienceTable v-if="resilience.length" :rows="resilience" />
        <p v-else class="faint none">No case with an injected fault was evaluated.</p>
      </section>

      <section class="section" aria-labelledby="injection-title">
        <h2 id="injection-title" class="section-title">Prompt injection</h2>
        <InjectionSummary v-if="injection.length" :items="injection" />
        <p v-else class="faint none">No prompt-injection case was evaluated.</p>
      </section>

      <section class="section" aria-labelledby="buckets-title">
        <h2 id="buckets-title" class="section-title">Failure buckets</h2>
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
</style>
