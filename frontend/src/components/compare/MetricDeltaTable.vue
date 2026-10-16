<script setup lang="ts">
import type { MetricDelta } from '@/api/types'
import StatusBadge from '@/components/ui/StatusBadge.vue'
import { direction, formatDelta, formatSide, metricLabel } from './deltas'

/*
 * A against B for each aggregate metric, over the cases both sides ran (n). Deltas are in
 * percentage points or counts, never relative percentages, and are coloured by whether the
 * change is good for that metric (fewer errors is better), always with a text label.
 */
defineProps<{ deltas: MetricDelta[]; deterministic: boolean }>()
</script>

<template>
  <div class="table-wrap panel">
    <table class="data-table">
      <caption class="visually-hidden">Metric deltas from A to B</caption>
      <thead>
        <tr>
          <th scope="col">Metric</th>
          <th scope="col" class="num">A</th>
          <th scope="col" class="num">B</th>
          <th scope="col" class="num">B − A</th>
          <th scope="col" class="num">n</th>
          <th scope="col">Change</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="d in deltas" :key="d.metric" :class="direction(d).tone" :data-metric="d.metric">
          <th scope="row">
            {{ metricLabel(d) }}
            <span v-if="d.metric === 'unauthorized_tool_attempts' && deterministic" class="note">simulated susceptibility</span>
            <span v-else-if="d.metric === 'unauthorized_tool_executions'" class="note">enforced by runtime permissions, must be 0</span>
          </th>
          <td class="num mono">{{ formatSide(d, d.a) }}</td>
          <td class="num mono">{{ formatSide(d, d.b) }}</td>
          <td class="num mono delta">{{ formatDelta(d) }}</td>
          <td class="num mono faint">{{ d.n }}</td>
          <td><StatusBadge :outcome="direction(d)" variant="plain" /></td>
        </tr>
      </tbody>
    </table>
  </div>
</template>

<style scoped>
th[scope='row'] {
  font-weight: 400;
}
.note {
  display: block;
  font-size: 11px;
  color: var(--text-muted);
}
.ok .delta {
  color: var(--ok);
  font-weight: 600;
}
.fail .delta {
  color: var(--fail);
  font-weight: 600;
}
.na .delta {
  color: var(--text-muted);
}
</style>
