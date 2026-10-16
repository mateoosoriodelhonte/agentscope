<script setup lang="ts">
import { computed } from 'vue'
import type { TraceRecord } from '@/api/types'
import CopyButton from '@/components/ui/CopyButton.vue'
import Metric from '@/components/ui/Metric.vue'
import SimulatedBadge from '@/components/ui/SimulatedBadge.vue'
import StatusBadge from '@/components/ui/StatusBadge.vue'
import { formatDuration, formatTimestamp, shortId } from '@/composables/format'
import { SIMULATED_PROVIDER } from '@/composables/useTraceModel'

const props = defineProps<{ trace: TraceRecord }>()

/** Provider-reported tokens only; partial coverage is stated, never extrapolated. */
const tokens = computed(() => {
  const usage = props.trace.usage
  if (!usage || usage.reported_calls === 0 || usage.input_tokens === null || usage.output_tokens === null) {
    return null
  }
  const coverage = usage.reported_calls < usage.total_calls ? ` (${usage.reported_calls}/${usage.total_calls} calls)` : ''
  return `${usage.input_tokens.toLocaleString()} in · ${usage.output_tokens.toLocaleString()} out${coverage}`
})

const cost = computed(() => {
  const estimate = props.trace.cost
  if (!estimate) return null
  return `$${estimate.amount.toFixed(4)}${estimate.partial ? ' (partial)' : ''}`
})

const toolCalls = computed(() => {
  const invocations = props.trace.tool_invocations
  const blocked = invocations.filter((i) => !i.executed).length
  return `${invocations.length - blocked} executed${blocked ? ` · ${blocked} blocked` : ''}`
})

const faults = computed(() => [
  ...props.trace.faults.map(
    (f) => `${f.tool}: ${f.kind}${f.on_call ? ` on call ${f.on_call}` : ' on every call'}${f.times && f.times > 1 ? ` ×${f.times}` : ''}`,
  ),
  ...props.trace.model_faults.map((f) => `model: ${f.kind} on response ${f.on_call}${f.times > 1 ? ` ×${f.times}` : ''}`),
])
</script>

<template>
  <header class="run-header">
    <div class="title-row">
      <StatusBadge :status="trace.status" />
      <h1 tabindex="-1" :title="trace.task">{{ trace.task }}</h1>
      <SimulatedBadge v-if="trace.provider === SIMULATED_PROVIDER" class="simulated" />
    </div>
    <div class="meta">
      <span class="mono strong">{{ trace.agent }}@{{ trace.agent_version }}</span>
      <span class="mono">{{ trace.provider }} / {{ trace.model }}</span>
      <span class="hash mono" :title="trace.config_hash">
        config {{ shortId(trace.config_hash.replace('sha256:', ''), 12) }}
        <CopyButton :value="trace.config_hash" label="Copy config hash" />
      </span>
      <span class="hash mono" :title="trace.run_id">
        run {{ shortId(trace.run_id) }}
        <CopyButton :value="trace.run_id" label="Copy run id" />
      </span>
      <span class="faint">{{ formatTimestamp(trace.started_at) }}</span>
    </div>
    <dl class="metrics">
      <Metric label="Steps" :value="`${trace.steps} / ${trace.limits.max_steps}`" />
      <Metric label="Duration" :value="formatDuration(trace.duration_ms)" />
      <Metric label="Tool calls" :value="toolCalls" />
      <Metric
        label="Tokens"
        :value="tokens"
        missing="not reported"
        hint="Token counts are shown only when the provider reports them; nothing is estimated."
      />
      <Metric
        label="Cost"
        :value="cost"
        missing="not configured"
        hint="Cost is shown only when per-model rates are configured and the provider reports tokens."
      />
      <div v-if="faults.length" class="faults">
        <dt>Injected faults</dt>
        <dd>
          <span v-for="f in faults" :key="f" class="fault mono">{{ f }}</span>
        </dd>
      </div>
    </dl>
    <p v-if="trace.error" class="error" role="status">
      <span class="mono">{{ trace.error_code }}</span> {{ trace.error }}
    </p>
  </header>
</template>

<style scoped>
.run-header {
  display: grid;
  gap: 6px;
}
.title-row {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px 10px;
  min-width: 0;
}
h1 {
  flex: 1 1 360px;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 17px;
}
.simulated {
  flex: none;
  margin-left: auto;
}
.meta {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 4px 14px;
  color: var(--text-muted);
  font-size: 12.5px;
}
.strong {
  color: var(--text);
}
.hash {
  display: inline-flex;
  align-items: center;
  gap: 2px;
}
.metrics {
  display: flex;
  flex-wrap: wrap;
  gap: 8px 28px;
  margin: 4px 0 0;
}
.faults dt {
  font-size: 11px;
  color: var(--text-muted);
}
.faults dd {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
  margin: 1px 0 0;
}
.fault {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 0 6px 0 0;
  overflow: hidden;
  border: 1px solid var(--injected);
  border-radius: var(--radius-sm);
  background: var(--injected-soft);
  color: var(--injected);
  font-size: 11.5px;
}
.fault::before {
  content: '';
  align-self: stretch;
  width: 8px;
  background: var(--hatch);
}
.error {
  margin: 2px 0 0;
  color: var(--fail);
  font-size: 12.5px;
}
</style>
