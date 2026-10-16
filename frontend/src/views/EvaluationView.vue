<script setup lang="ts">
import { computed, onBeforeUnmount, watch } from 'vue'
import { RouterLink } from 'vue-router'
import { api } from '@/api/client'
import { describeError } from '@/api/errors'
import CaseCheckMatrix from '@/components/evaluation/CaseCheckMatrix.vue'
import GateChecklist from '@/components/evaluation/GateChecklist.vue'
import CopyButton from '@/components/ui/CopyButton.vue'
import ErrorState from '@/components/ui/ErrorState.vue'
import Metric from '@/components/ui/Metric.vue'
import SimulatedBadge from '@/components/ui/SimulatedBadge.vue'
import Skeleton from '@/components/ui/Skeleton.vue'
import StatusBadge from '@/components/ui/StatusBadge.vue'
import { formatDuration, formatTimestamp, plural, ratio, shortId } from '@/composables/format'
import { jobProgress, useJob } from '@/composables/useJob'
import { setPageDetail } from '@/composables/usePageMeta'
import { param, useQueryState } from '@/composables/useQueryState'
import { useResource } from '@/composables/useResource'

const props = defineProps<{ suiteId: string }>()
const suite = useResource((signal) => api.getEvaluation(props.suiteId, signal), () => props.suiteId)
const { state: query, set } = useQueryState({
  /** The job that is producing this suite, when the page was opened right after starting it. */
  job: param.string(),
  failing: param.boolean(),
})
const progress = useJob()

// Follow the job that is filling this suite, then show the finished result.
watch(
  () => query.value.job,
  async (jobId) => {
    if (!jobId) return
    const done = await progress.follow(jobId)
    if (done) {
      await set({ job: null })
      await suite.reload()
    }
  },
  { immediate: true },
)

// A suite still running without a job to follow (opened from the list) refreshes itself:
// every reload delivers a new result (or a new error, which keeps the last result on
// screen), and each schedules the next reload until the suite finishes.
let refresh: ReturnType<typeof setTimeout> | undefined
watch(
  [() => suite.data.value, () => suite.error.value],
  ([value]) => {
    clearTimeout(refresh)
    if (value?.status === 'running' && !query.value.job) refresh = setTimeout(() => void suite.reload(), 2000)
  },
)
onBeforeUnmount(() => clearTimeout(refresh))

const data = computed(() => suite.data.value)
const title = computed(() => data.value?.label || `Evaluation ${shortId(props.suiteId)}`)
const agentRef = computed(() => (data.value ? `${data.value.agent_config.agent}@${data.value.agent_config.version}` : ''))
const deterministic = computed(() => data.value?.environment.deterministic ?? false)
const cases = computed(() => (data.value?.cases ?? []).filter((c) => !query.value.failing || !c.passed))
const exportUrl = computed(() => {
  try {
    return api.evaluationExportUrl(props.suiteId)
  } catch {
    return null
  }
})
const statuses = computed(() =>
  Object.entries(data.value?.metrics.run_statuses ?? {})
    .map(([status, n]) => `${status.replace('_', ' ')} ${n}`)
    .join(' · '),
)
const usage = computed(() => {
  const u = data.value?.metrics.usage
  if (!u || u.reporting_runs === 0) return null
  const partial = u.reporting_runs < u.total_runs ? ` (reported for ${ratio(u.reporting_runs, u.total_runs)} runs)` : ''
  return `${u.input_tokens.toLocaleString()} in · ${u.output_tokens.toLocaleString()} out${partial}`
})
const jobError = computed(() => (progress.error.value ? describeError(progress.error.value).message : null))
/** While the job is still creating the suite, a 404 means "not yet", not "missing". */
const waiting = computed(() => !data.value && progress.running.value)

watch(data, (value) => setPageDetail(value ? title.value : null), { immediate: true })
</script>

<template>
  <div class="screen">
    <header class="screen-head">
      <div>
        <div class="title-row">
          <h1 tabindex="-1">{{ title }}</h1>
          <StatusBadge v-if="data" :status="data.status" />
        </div>
        <p v-if="data" class="subtitle">
          <RouterLink :to="`/datasets/${encodeURIComponent(data.dataset_name)}/${encodeURIComponent(data.dataset_version)}`" class="mono">
            {{ data.dataset_name }}@{{ data.dataset_version }}
          </RouterLink>
          ·
          <RouterLink :to="`/agents/${encodeURIComponent(data.agent_config.agent)}/${encodeURIComponent(data.agent_config.version)}`" class="mono">
            {{ agentRef }}
          </RouterLink>
          · <span class="mono">{{ data.environment.provider }} / {{ data.environment.model }}</span>
          · started {{ formatTimestamp(data.started_at) }}
        </p>
      </div>
      <div v-if="data" class="actions">
        <SimulatedBadge v-if="deterministic" />
        <RouterLink :to="{ path: '/compare', query: { a: data.suite_id } }" class="btn">Compare…</RouterLink>
        <a v-if="exportUrl" :href="exportUrl" class="btn" download>Export JSON</a>
      </div>
    </header>

    <p v-if="progress.running.value" class="callout info" role="status">
      Running: {{ jobProgress(progress.job.value) }} cases evaluated.
    </p>
    <p v-else-if="jobError" class="callout fail" role="alert">The evaluation job failed: {{ jobError }}</p>
    <p v-else-if="data?.status === 'running'" class="callout info" role="status">
      Still running: {{ plural(data.cases.length, 'case') }} recorded so far. This page refreshes itself.
    </p>

    <Skeleton v-if="waiting" :rows="6" />
    <ErrorState v-else-if="suite.error.value && !data" :error="suite.error.value" title="Could not load this evaluation" @retry="suite.reload">
      <template #actions>
        <RouterLink to="/evaluations" class="btn">All evaluations</RouterLink>
      </template>
    </ErrorState>
    <Skeleton v-else-if="!data" :rows="8" />

    <template v-else>
      <div class="columns">
        <GateChecklist :gate="data.gate" />
        <section class="section" aria-labelledby="metrics-title">
          <h2 id="metrics-title" class="section-title">Metrics</h2>
          <dl class="metrics panel">
            <Metric label="Passed" :value="ratio(data.metrics.passed, data.metrics.total)" />
            <Metric label="Failed" :value="ratio(data.metrics.failed, data.metrics.total)" />
            <Metric label="Harness errors" :value="ratio(data.metrics.harness_errors, data.metrics.total)" hint="Cases the harness could not run: not agent failures." />
            <Metric label="Run statuses" :value="statuses || '—'" />
            <Metric label="Invalid outputs" :value="ratio(data.metrics.invalid_outputs, data.metrics.total)" />
            <Metric label="Repaired outputs" :value="ratio(data.metrics.repaired_outputs, data.metrics.total)" />
            <Metric label="Cases with policy violations" :value="ratio(data.metrics.policy_violation_cases, data.metrics.total)" />
            <Metric
              :label="deterministic ? 'Unauthorized attempts (simulated susceptibility)' : 'Unauthorized attempts'"
              :value="String(data.metrics.unauthorized_tool_attempts)"
              hint="Tool calls outside the permitted set that the model tried, blocked or not."
            />
            <div class="metric-strong" :class="{ bad: data.metrics.unauthorized_tool_executions > 0 }">
              <dt>Unauthorized executions (enforced by runtime permissions, must be 0)</dt>
              <dd class="mono">{{ data.metrics.unauthorized_tool_executions }}</dd>
            </div>
            <Metric label="Injected fault outcomes" :value="String(data.metrics.injected_fault_outcomes)" />
            <Metric label="Unexpected tool errors" :value="String(data.metrics.unexpected_tool_errors)" />
            <Metric label="Average steps" :value="data.metrics.avg_steps.toFixed(1)" />
            <Metric label="Tokens" :value="usage" missing="not reported" hint="Only what the provider reported; nothing is estimated." />
          </dl>
          <p v-if="data.metrics.timings" class="faint timings">
            Informational, not deterministic: median {{ formatDuration(data.metrics.timings.median_duration_ms) }}, p95
            {{ formatDuration(data.metrics.timings.p95_duration_ms) }} per case.
          </p>
        </section>
      </div>

      <section class="section" aria-labelledby="matrix-title">
        <div class="section-head">
          <h2 id="matrix-title" class="section-title">Cases × checks</h2>
          <div class="actions">
            <label class="check">
              <input type="checkbox" :checked="query.failing === true" @change="set({ failing: ($event.target as HTMLInputElement).checked || null })" />
              Only failing cases
            </label>
          </div>
        </div>
        <p v-if="!data.cases.length" class="faint none">No case has finished yet.</p>
        <p v-else-if="!cases.length" class="faint none">Every case passed.</p>
        <CaseCheckMatrix v-else :suite-id="data.suite_id" :cases="cases" :stats="data.metrics.checks" />
      </section>

      <details class="environment panel">
        <summary>Reproducibility fingerprint</summary>
        <dl class="fingerprint">
          <div><dt>Dataset hash</dt><dd class="mono">{{ data.dataset_hash }} <CopyButton :value="data.dataset_hash" label="Copy the dataset hash" /></dd></div>
          <div><dt>Config hash</dt><dd class="mono">{{ data.config_hash }} <CopyButton :value="data.config_hash" label="Copy the config hash" /></dd></div>
          <div><dt>Effective config hash</dt><dd class="mono">{{ data.effective_config_hash }}</dd></div>
          <div><dt>Checks enabled</dt><dd class="mono">{{ data.checks_enabled.join(', ') }}</dd></div>
          <div><dt>Check engine</dt><dd class="mono">{{ data.environment.check_engine_version }}</dd></div>
          <div><dt>AgentScope</dt><dd class="mono">{{ data.environment.agentscope_version }}</dd></div>
          <div><dt>Python · LangGraph</dt><dd class="mono">{{ data.environment.python_version }} · {{ data.environment.langgraph_version }}</dd></div>
          <div><dt>Tool data hash</dt><dd class="mono">{{ data.environment.data_hash }}</dd></div>
        </dl>
      </details>
    </template>
  </div>
</template>

<style scoped>
.title-row {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
}
.columns {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
  gap: 18px;
  align-items: start;
}
.metrics {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 10px 20px;
  margin: 0;
  padding: 12px 14px;
}
.metrics :deep(dd) {
  white-space: normal;
}
.metric-strong {
  grid-column: 1 / -1;
  padding: 6px 8px;
  border-radius: var(--radius);
  background: var(--surface-2);
}
.metric-strong dt {
  font-size: 11px;
  color: var(--text-muted);
}
.metric-strong dd {
  margin: 0;
  font-size: 13px;
}
.metric-strong.bad {
  background: var(--fail-soft);
}
.metric-strong.bad dd {
  color: var(--fail);
  font-weight: 600;
}
.timings {
  margin: 0;
  font-size: 12px;
}
.check {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 12.5px;
}
.none {
  margin: 0;
  font-size: 13px;
}
.environment {
  padding: 8px 12px;
  font-size: 13px;
}
.environment summary {
  cursor: pointer;
  color: var(--text-muted);
}
.fingerprint {
  display: grid;
  gap: 6px;
  margin: 10px 0 2px;
}
.fingerprint div {
  display: grid;
  grid-template-columns: 180px minmax(0, 1fr);
  gap: 10px;
}
.fingerprint dt {
  color: var(--text-muted);
}
.fingerprint dd {
  margin: 0;
  overflow-wrap: anywhere;
}
</style>
