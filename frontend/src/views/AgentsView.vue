<script setup lang="ts">
import { RouterLink } from 'vue-router'
import { api } from '@/api/client'
import type { AgentConfig } from '@/api/types'
import EmptyState from '@/components/ui/EmptyState.vue'
import ErrorState from '@/components/ui/ErrorState.vue'
import Skeleton from '@/components/ui/Skeleton.vue'
import { openNewRun } from '@/composables/newRun'
import { useResource } from '@/composables/useResource'

const catalog = useResource((signal) => api.agents(signal))

const versionLink = (v: AgentConfig) => `/agents/${encodeURIComponent(v.agent)}/${encodeURIComponent(v.version)}`
const retryLabel = (v: AgentConfig) =>
  v.retry.max_tool_retries ? `${v.retry.max_tool_retries}× on ${v.retry.retry_on.join(', ')}` : 'none'
</script>

<template>
  <div class="screen">
    <header class="screen-head">
      <div>
        <h1 tabindex="-1">Agents</h1>
        <p class="subtitle">Built-in agent versions. Everything that differs between two versions is in their configuration.</p>
      </div>
    </header>

    <ErrorState v-if="catalog.error.value && !catalog.data.value" :error="catalog.error.value" title="Could not load the agents" @retry="catalog.reload" />
    <Skeleton v-else-if="!catalog.data.value" :rows="6" />
    <EmptyState v-else-if="!catalog.data.value.agents.length" title="No agents registered" variant="first-run">
      <p>This AgentScope build registers no agents.</p>
    </EmptyState>

    <template v-else>
      <section v-for="agent in catalog.data.value.agents" :key="agent.name" class="section" :aria-labelledby="`agent-${agent.name}`">
        <div class="section-head">
          <h2 :id="`agent-${agent.name}`" class="agent-title">{{ agent.title }}</h2>
          <span class="mono faint">{{ agent.name }}</span>
        </div>
        <p class="description">{{ agent.description }}</p>
        <p class="toolset">
          <span class="faint">Toolset:</span>
          <span v-for="tool in agent.toolset" :key="tool" class="tag">{{ tool }}</span>
        </p>
        <div class="table-wrap panel">
          <table class="data-table">
            <caption class="visually-hidden">Versions of {{ agent.title }}</caption>
            <thead>
              <tr>
                <th scope="col">Version</th>
                <th scope="col">Model</th>
                <th scope="col">Graph</th>
                <th scope="col">Untrusted output</th>
                <th scope="col">Tool retries</th>
                <th scope="col" class="num">Tools</th>
                <th scope="col"><span class="visually-hidden">Actions</span></th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="v in agent.versions" :key="v.version">
                <th scope="row">
                  <RouterLink :to="versionLink(v)" class="mono">{{ v.agent }}@{{ v.version }}</RouterLink>
                  <span v-if="v.label" class="label">{{ v.label }}</span>
                </th>
                <td class="mono">{{ v.model.provider }} / {{ v.model.model }}</td>
                <td>{{ v.graph }}</td>
                <td>{{ v.isolate_untrusted ? 'delimited' : 'not delimited' }}</td>
                <td>{{ retryLabel(v) }}</td>
                <td class="num mono">{{ v.tools.length }}</td>
                <td class="num">
                  <button type="button" class="btn" @click="openNewRun({ agentRef: `${v.agent}@${v.version}` })">
                    New run
                  </button>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </section>
    </template>
  </div>
</template>

<style scoped>
.agent-title {
  font-size: 15px;
}
.description {
  margin: 0;
  max-width: 80ch;
  color: var(--text-muted);
  font-size: 13px;
}
.toolset {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 4px;
  margin: 0;
  font-size: 12.5px;
}
.label {
  display: block;
  font-size: 12px;
  font-weight: 400;
  color: var(--text-muted);
}
</style>
