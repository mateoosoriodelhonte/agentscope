<script setup lang="ts">
import { computed, watch } from 'vue'
import { RouterLink } from 'vue-router'
import { api } from '@/api/client'
import TopologyDiagram from '@/components/agents/TopologyDiagram.vue'
import CopyButton from '@/components/ui/CopyButton.vue'
import EmptyState from '@/components/ui/EmptyState.vue'
import ErrorState from '@/components/ui/ErrorState.vue'
import JsonView from '@/components/ui/JsonView.vue'
import { asJson } from '@/components/ui/jsonLines'
import Metric from '@/components/ui/Metric.vue'
import SimulatedBadge from '@/components/ui/SimulatedBadge.vue'
import Skeleton from '@/components/ui/Skeleton.vue'
import ViewToggle from '@/components/ui/ViewToggle.vue'
import { formatTimestamp, shortId } from '@/composables/format'
import { openNewRun } from '@/composables/newRun'
import { setPageDetail } from '@/composables/usePageMeta'
import { param, useQueryState } from '@/composables/useQueryState'
import { useResource } from '@/composables/useResource'

const props = defineProps<{ agent: string; version: string }>()
const agentRef = computed(() => `${props.agent}@${props.version}`)

const catalog = useResource((signal) => api.agents(signal))
/**
 * The config hash is computed by the server (canonical JSON of the config), so it is read
 * from the latest recorded run of this version rather than recomputed here. A page of runs
 * is fetched in case the server ignores the version filter.
 */
const latestRun = useResource(
  async (signal) =>
    (await api.listRuns({ agent: props.agent, agent_version: props.version, limit: 50 }, signal)).items.find(
      (run) => run.agent === props.agent && run.agent_version === props.version,
    ) ?? null,
  () => agentRef.value,
)
const { state: query, set } = useQueryState({ graph: param.enum(['chart', 'table'] as const) })

const info = computed(() => catalog.data.value?.agents.find((a) => a.name === props.agent) ?? null)
const config = computed(() => info.value?.versions.find((v) => v.version === props.version) ?? null)
const tools = computed(() => {
  const specs = catalog.data.value?.tools ?? []
  return (config.value?.tools ?? []).map((name) => ({ name, spec: specs.find((t) => t.name === name) ?? null }))
})
const prompt = computed(() => (config.value ? (info.value?.prompts[config.value.prompt_version] ?? null) : null))
const configJson = computed(() => (config.value ? JSON.stringify(config.value, null, 2) : ''))

watch(
  config,
  (value) => setPageDetail(value ? agentRef.value : null),
  { immediate: true },
)
</script>

<template>
  <div class="screen">
    <header class="screen-head">
      <div>
        <h1 tabindex="-1" class="mono-title">{{ agentRef }}</h1>
        <p v-if="config" class="subtitle">{{ config.label }}<template v-if="config.description"> · {{ config.description }}</template></p>
      </div>
      <div v-if="config" class="actions">
        <SimulatedBadge v-if="config.model.provider === 'fake'" compact />
        <button type="button" class="btn primary" @click="openNewRun({ agentRef })">New run</button>
      </div>
    </header>

    <ErrorState v-if="catalog.error.value && !catalog.data.value" :error="catalog.error.value" title="Could not load the agents" @retry="catalog.reload" />
    <Skeleton v-else-if="!catalog.data.value" :rows="8" />
    <EmptyState v-else-if="!config" title="No such agent version" variant="error">
      <p><code>{{ agentRef }}</code> is not a built-in agent version.</p>
      <template #actions>
        <RouterLink to="/agents" class="btn">All agents</RouterLink>
      </template>
    </EmptyState>

    <template v-else>
      <dl class="summary panel">
        <Metric label="Provider / model" :value="`${config.model.provider} / ${config.model.model}`" />
        <Metric label="Temperature · seed" :value="`${config.model.temperature} · ${config.model.seed ?? 'none'}`" />
        <Metric label="Max output tokens" :value="String(config.model.max_output_tokens)" />
        <Metric label="Request timeout" :value="`${config.model.request_timeout_s} s`" />
        <Metric label="Graph" :value="config.graph" />
        <Metric label="Untrusted tool output" :value="config.isolate_untrusted ? 'delimited' : 'not delimited'" />
        <Metric label="Prompt version" :value="config.prompt_version" />
        <Metric label="Tool schema version" :value="String(config.tool_schema_version)" />
        <div class="hash">
          <dt>Config hash</dt>
          <dd v-if="latestRun.data.value" class="mono">
            <span :title="latestRun.data.value.config_hash">{{ shortId(latestRun.data.value.config_hash.replace('sha256:', ''), 16) }}</span>
            <CopyButton :value="latestRun.data.value.config_hash" label="Copy config hash" />
            <span class="faint source">
              as recorded by
              <RouterLink :to="`/runs/${encodeURIComponent(latestRun.data.value.run_id)}`">run {{ shortId(latestRun.data.value.run_id) }}</RouterLink>,
              {{ formatTimestamp(latestRun.data.value.started_at) }}
            </span>
          </dd>
          <dd v-else-if="latestRun.loading.value" class="faint">loading…</dd>
          <dd v-else class="faint">not recorded yet: the server computes it on the first run of this version</dd>
        </div>
      </dl>

      <section class="section" aria-labelledby="topology-title">
        <div class="section-head">
          <h2 id="topology-title" class="section-title">Graph topology</h2>
          <div class="actions">
            <ViewToggle
              :model-value="query.graph ?? 'chart'"
              label="Show the topology as"
              @update:model-value="set({ graph: $event === 'chart' ? null : $event })"
            />
          </div>
        </div>
        <TopologyDiagram :current="config.graph" :view="query.graph ?? 'chart'" />
      </section>

      <div class="columns">
        <section class="section" aria-labelledby="retry-title">
          <h2 id="retry-title" class="section-title">Retry policy</h2>
          <table class="data-table panel kv">
            <tbody>
              <tr><th scope="row">Tool retries</th><td class="mono">{{ config.retry.max_tool_retries }}</td></tr>
              <tr><th scope="row">Retry on</th><td class="mono">{{ config.retry.retry_on.join(', ') || 'nothing' }}</td></tr>
              <tr><th scope="row">Decision repairs</th><td class="mono">{{ config.retry.max_decision_repairs }}</td></tr>
              <tr><th scope="row">Output repairs</th><td class="mono">{{ config.retry.max_output_repairs }}</td></tr>
            </tbody>
          </table>
        </section>
        <section class="section" aria-labelledby="limits-title">
          <h2 id="limits-title" class="section-title">Limits</h2>
          <table class="data-table panel kv">
            <tbody>
              <tr><th scope="row">Max steps</th><td class="mono">{{ config.limits.max_steps }}</td></tr>
              <tr><th scope="row">Tool timeout</th><td class="mono">{{ config.limits.tool_timeout_s }} s</td></tr>
              <tr><th scope="row">Run timeout</th><td class="mono">{{ config.limits.run_timeout_s }} s</td></tr>
              <tr><th scope="row">Max tool output</th><td class="mono">{{ config.limits.max_tool_output_chars.toLocaleString() }} chars</td></tr>
            </tbody>
          </table>
        </section>
      </div>

      <section class="section" aria-labelledby="tools-title">
        <h2 id="tools-title" class="section-title">Permitted tools</h2>
        <p v-if="!tools.length" class="faint none">This version permits no tools.</p>
        <details v-for="tool in tools" :key="tool.name" class="tool panel">
          <summary>
            <span class="mono name">{{ tool.name }}</span>
            <span v-if="tool.spec" class="muted">{{ tool.spec.description }}</span>
            <span v-if="tool.spec?.side_effect === 'sandbox_write'" class="tag">sandbox write</span>
          </summary>
          <JsonView v-if="tool.spec" :value="tool.spec.parameters" :label="`JSON schema of ${tool.name}`" />
          <p v-else class="faint">This tool is not in the registry.</p>
        </details>
      </section>

      <section class="section" aria-labelledby="prompt-title">
        <h2 id="prompt-title" class="section-title">Prompt {{ config.prompt_version }}</h2>
        <pre v-if="prompt !== null" class="prompt" tabindex="0" aria-label="Prompt text">{{ prompt }}</pre>
        <p v-else class="faint none">The prompt text for {{ config.prompt_version }} is not available.</p>
      </section>

      <section class="section" aria-labelledby="config-title">
        <div class="section-head">
          <h2 id="config-title" class="section-title">Configuration</h2>
          <div class="actions"><CopyButton :value="configJson" label="Copy the configuration as JSON" /></div>
        </div>
        <JsonView :value="asJson(config)" label="Agent configuration" />
      </section>

      <section v-if="info" class="section" aria-labelledby="output-title">
        <h2 id="output-title" class="section-title">Output schema</h2>
        <JsonView :value="info.output_schema" label="Output JSON schema" />
      </section>
    </template>
  </div>
</template>

<style scoped>
.mono-title {
  font-family: var(--font-mono);
  font-size: 17px;
}
.summary {
  display: flex;
  flex-wrap: wrap;
  gap: 10px 28px;
  margin: 0;
  padding: 12px 14px;
}
.hash dt {
  font-size: 11px;
  color: var(--text-muted);
}
.hash dd {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 4px;
  margin: 1px 0 0;
  font-size: 13px;
}
.source {
  font-family: var(--font-ui);
  font-size: 12px;
}
.columns {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 18px;
}
.kv th {
  width: 50%;
  font-weight: 400;
  color: var(--text-muted);
}
.none {
  margin: 0;
  font-size: 13px;
}
.tool {
  padding: 8px 12px;
}
.tool summary {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px 10px;
  cursor: pointer;
  font-size: 13px;
}
.tool[open] summary {
  margin-bottom: 8px;
}
.name {
  color: var(--text);
}
.prompt {
  margin: 0;
  padding: 10px 12px;
  max-height: 420px;
  overflow: auto;
  border: 1px solid var(--border);
  border-radius: var(--radius);
  background: var(--surface-2);
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  font-family: var(--font-mono);
  font-size: 12px;
  line-height: 1.55;
}
</style>
