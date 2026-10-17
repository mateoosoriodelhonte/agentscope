<script setup lang="ts">
import { RouterLink } from 'vue-router'
import type { SuiteListItem } from '@/api/types'
import SimulatedBadge from '@/components/ui/SimulatedBadge.vue'
import StatusBadge from '@/components/ui/StatusBadge.vue'
import { formatTimestamp, ratio, shortId } from '@/composables/format'
import { gateOutcome } from './gate'

// Evaluations (suite runs) as rows: the pass rate is always k/n, never a bare percentage.
defineProps<{ suites: SuiteListItem[]; caption: string }>()
</script>

<template>
  <div class="table-wrap panel">
    <table class="data-table">
      <caption class="visually-hidden">{{ caption }}</caption>
      <thead>
        <tr>
          <th scope="col">Status</th>
          <th scope="col">Evaluation</th>
          <th scope="col">Dataset</th>
          <th scope="col">Agent · model</th>
          <th scope="col" class="num">Passed</th>
          <th scope="col">Gate</th>
          <th scope="col">Started</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="suite in suites" :key="suite.suite_id">
          <td><StatusBadge :status="suite.status" /></td>
          <td>
            <RouterLink :to="`/evaluations/${encodeURIComponent(suite.suite_id)}`" class="name">
              {{ suite.label || `Evaluation ${shortId(suite.suite_id)}` }}
            </RouterLink>
          </td>
          <td class="mono">{{ suite.dataset_name }}@{{ suite.dataset_version }}</td>
          <td>
            <span class="mono strong">{{ suite.agent_ref }}</span>
            <span class="model">
              <span class="mono">{{ suite.model }}</span>
              <SimulatedBadge v-if="suite.deterministic" compact />
              <span v-else class="faint">{{ suite.provider }}</span>
            </span>
          </td>
          <td class="num mono">{{ ratio(suite.passed, suite.total) }}</td>
          <td><StatusBadge :outcome="gateOutcome(suite.gate_passed)" variant="plain" /></td>
          <td class="faint when">{{ formatTimestamp(suite.started_at) }}</td>
        </tr>
      </tbody>
    </table>
  </div>
</template>

<style scoped>
.name {
  color: var(--text);
  font-weight: 500;
}
.name:hover {
  color: var(--accent);
}
.strong {
  display: block;
  color: var(--text);
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
.when {
  font-size: 12px;
  white-space: nowrap;
}
</style>
