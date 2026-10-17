<script setup lang="ts">
import { computed, ref, useId } from 'vue'
import { RouterLink } from 'vue-router'
import { api, ApiError } from '@/api/client'
import TraceDiff from '@/components/compare/TraceDiff.vue'
import ErrorState from '@/components/ui/ErrorState.vue'
import Skeleton from '@/components/ui/Skeleton.vue'
import { shortId } from '@/composables/format'
import { param, useQueryState } from '@/composables/useQueryState'
import { useResource } from '@/composables/useResource'

/** Any two runs, aligned step by step. `?a=` and `?b=` are full run ids. */
const { state: query, set } = useQueryState({ a: param.string(), b: param.string() })
// Recent runs as suggestions: lists elsewhere show shortened ids, the API needs full ones.
const recent = useResource((signal) => api.listRuns({ limit: 50 }, signal))
const suggestions = useId()
const runs = useResource(
  async (signal) => {
    const { a, b } = query.value
    if (!a || !b) return null
    const [left, right] = await Promise.all([api.getRun(a, signal), api.getRun(b, signal)])
    return { a: left, b: right }
  },
  () => `${query.value.a}|${query.value.b}`,
)
const FULL_ID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i
/** A run that was not found by an id that is not a full one: most likely a shortened id. */
const shortened = computed(() => {
  const failure = runs.error.value
  if (!(failure instanceof ApiError) || failure.status !== 404) return false
  return [query.value.a, query.value.b].some((id) => id && !FULL_ID.test(id))
})
const inputA = ref(query.value.a ?? '')
const inputB = ref(query.value.b ?? '')
const ready = computed(() => Boolean(query.value.a && query.value.b))
const refOf = (side: 'a' | 'b') => {
  const trace = runs.data.value?.[side].trace
  return trace ? `${trace.agent}@${trace.agent_version}` : ''
}

function submit(): void {
  if (inputA.value.trim() && inputB.value.trim()) void set({ a: inputA.value.trim(), b: inputB.value.trim() }, { push: true })
}
</script>

<template>
  <div class="screen">
    <header class="screen-head">
      <div>
        <h1 tabindex="-1">Compare runs</h1>
        <p v-if="runs.data.value" class="subtitle">
          A <RouterLink :to="`/runs/${encodeURIComponent(runs.data.value.a.trace.run_id)}`" class="mono">{{ refOf('a') }} · {{ shortId(runs.data.value.a.trace.run_id) }}</RouterLink>
          → B <RouterLink :to="`/runs/${encodeURIComponent(runs.data.value.b.trace.run_id)}`" class="mono">{{ refOf('b') }} · {{ shortId(runs.data.value.b.trace.run_id) }}</RouterLink>
        </p>
        <p v-else class="subtitle">A step-aligned diff of two traces that names the first divergence.</p>
      </div>
    </header>

    <form class="pick" aria-label="Choose two runs" @submit.prevent="submit">
      <label class="field">
        <span>Run A</span>
        <input v-model="inputA" class="input mono-input" spellcheck="false" :list="suggestions" />
      </label>
      <label class="field">
        <span>Run B</span>
        <input v-model="inputB" class="input mono-input" spellcheck="false" :list="suggestions" />
      </label>
      <button type="submit" class="btn" :disabled="!inputA.trim() || !inputB.trim()">Compare</button>
      <datalist :id="suggestions">
        <option v-for="run in recent.data.value?.items ?? []" :key="run.run_id" :value="run.run_id">
          {{ run.agent }}@{{ run.agent_version }} · {{ run.task }}
        </option>
      </datalist>
    </form>

    <p v-if="!ready" class="faint none">
      Enter two full run ids (recent runs are suggested as you type), or open a case from a comparison. On the
      <RouterLink to="/runs">Runs</RouterLink> page, the copy button next to each run copies its full id.
    </p>
    <template v-else-if="runs.error.value && !runs.data.value">
      <p v-if="shortened" class="callout warn short-ids" role="note">
        A run is found only by its full run id (36 characters); lists show just the first 8. Copy the full id from the
        Runs page, or pick a suggestion.
      </p>
      <ErrorState :error="runs.error.value" title="Could not load the two runs" @retry="runs.reload" />
    </template>
    <Skeleton v-else-if="!runs.data.value" :rows="8" />
    <TraceDiff v-else :a="runs.data.value.a" :b="runs.data.value.b" :label-a="`A ${refOf('a')}`" :label-b="`B ${refOf('b')}`" />
  </div>
</template>

<style scoped>
.pick {
  display: flex;
  flex-wrap: wrap;
  align-items: flex-end;
  gap: 10px;
}
.mono-input {
  width: 320px;
  font-family: var(--font-mono);
  font-size: 12px;
}
.none {
  margin: 0;
  font-size: 13px;
}
</style>
