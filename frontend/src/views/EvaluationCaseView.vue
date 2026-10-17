<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { RouterLink } from 'vue-router'
import { api } from '@/api/client'
import EmptyState from '@/components/ui/EmptyState.vue'
import ErrorState from '@/components/ui/ErrorState.vue'
import Skeleton from '@/components/ui/Skeleton.vue'
import StatusBadge from '@/components/ui/StatusBadge.vue'
import { keepHeadingFocus } from '@/composables/focus'
import { shortId } from '@/composables/format'
import { setPageDetail, setPageTrail } from '@/composables/usePageMeta'
import { useResource } from '@/composables/useResource'
import TraceView from './TraceView.vue'

/*
 * One case of an evaluation: the case's trace, opened in the trace explorer, under a
 * suite › case › trace breadcrumb. `?check=` passes through, so a matrix cell opens the
 * check's evidence. A case with no run (a harness error, or a purged run) shows its checks.
 */
const props = defineProps<{ suiteId: string; caseId: string }>()
const page = ref<HTMLElement | null>(null)
const suite = useResource((signal) => api.getEvaluation(props.suiteId, signal), () => props.suiteId)

const result = computed(() => suite.data.value?.cases.find((c) => c.case_id === props.caseId) ?? null)
const suiteLabel = computed(() => suite.data.value?.label || `Evaluation ${shortId(props.suiteId)}`)
const suiteLink = computed(() => `/evaluations/${encodeURIComponent(props.suiteId)}`)
/** The run id once it is known, swapped in with focus kept on the page heading. */
const runId = ref<string | null>(null)

watch(
  [suite.data, () => props.caseId],
  async () => {
    if (!suite.data.value) return
    setPageTrail([
      { label: suiteLabel.value, to: suiteLink.value },
      { label: props.caseId, to: `${suiteLink.value}/cases/${encodeURIComponent(props.caseId)}` },
    ])
    const next = result.value?.run_id ?? null
    if (next === runId.value) return
    if (!next) setPageDetail('no trace')
    await keepHeadingFocus(page.value, () => (runId.value = next))
  },
  { immediate: true },
)
</script>

<template>
  <div ref="page" class="case-page">
    <TraceView v-if="runId" :key="runId" :run-id="runId" />
    <div v-else class="screen">
      <h1 tabindex="-1">Case {{ caseId }}</h1>
      <ErrorState v-if="suite.error.value && !suite.data.value" :error="suite.error.value" title="Could not load this evaluation" @retry="suite.reload" />
      <Skeleton v-else-if="!suite.data.value" :rows="8" />
      <EmptyState v-else-if="!result" title="No such case in this evaluation" variant="error">
        <p><code>{{ caseId }}</code> is not a case of {{ suiteLabel }}.</p>
        <template #actions>
          <RouterLink :to="suiteLink" class="btn">Back to the evaluation</RouterLink>
        </template>
      </EmptyState>
      <template v-else>
        <p class="callout warn">
          <span>
            This case has no trace:
            {{ result.harness_error ? `the harness could not run it (${result.harness_error}).` : 'its run was purged.' }}
            The verdict below is what was recorded.
          </span>
        </p>
        <ul class="checks">
          <li v-for="check in result.checks" :key="check.name" class="panel">
            <StatusBadge :status="check.status" variant="plain" />
            <span class="mono name">{{ check.name }}</span>
            <span class="muted">{{ check.message }}</span>
          </li>
        </ul>
      </template>
    </div>
  </div>
</template>

<style scoped>
.case-page {
  height: 100%;
}
.checks {
  display: grid;
  gap: 4px;
  margin: 0;
  padding: 0;
  list-style: none;
}
.checks li {
  display: grid;
  grid-template-columns: 120px 200px minmax(0, 1fr);
  align-items: center;
  gap: 8px;
  padding: 6px 10px;
  font-size: 13px;
}
.name {
  color: var(--text);
}
</style>
