<script setup lang="ts">
import { onMounted, ref, shallowRef } from 'vue'
import { RouterLink } from 'vue-router'
import { api } from '@/api/client'
import type { RunSummary } from '@/api/types'
import StatusBadge from '@/components/StatusBadge.vue'

const runs = shallowRef<RunSummary[]>([])
const total = ref(0)
const error = ref<string | null>(null)
const loading = ref(true)

onMounted(async () => {
  try {
    const result = await api.listRuns()
    runs.value = result.items
    total.value = result.total
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e)
  } finally {
    loading.value = false
  }
})
</script>

<template>
  <section>
    <h1>Runs</h1>
    <p v-if="error" class="error" role="alert">Could not load runs: {{ error }}</p>
    <p v-else-if="!loading && runs.length === 0" class="empty">
      No runs yet. Try <code>agentscope run --task "In what year did the Meridian Footbridge open?"</code>
    </p>
    <table v-else class="runs">
      <thead>
        <tr>
          <th>Status</th>
          <th>Agent</th>
          <th>Task</th>
          <th class="num">Steps</th>
          <th class="num">Tool calls</th>
          <th>Verdict</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="run in runs" :key="run.run_id">
          <td><StatusBadge :status="run.status" /></td>
          <td class="mono">{{ run.agent }}@{{ run.agent_version }}</td>
          <td><RouterLink :to="`/runs/${run.run_id}`">{{ run.task }}</RouterLink></td>
          <td class="num mono">{{ run.steps }}</td>
          <td class="num mono">
            {{ run.tool_calls }}<span v-if="run.blocked_tool_calls" class="blocked"> ({{ run.blocked_tool_calls }} blocked)</span>
          </td>
          <td>
            <StatusBadge v-if="run.passed !== null" :status="run.passed ? 'passed' : 'failed'" />
            <span v-else class="muted">—</span>
          </td>
        </tr>
      </tbody>
    </table>
  </section>
</template>

<style scoped>
h1 {
  font-size: 18px;
  margin-bottom: 16px;
}
.runs {
  width: 100%;
  border-collapse: collapse;
  background: var(--surface-1);
  border: 1px solid var(--border);
  border-radius: 8px;
}
th,
td {
  text-align: left;
  padding: 8px 12px;
  border-bottom: 1px solid var(--border);
}
th {
  font-size: 12px;
  font-weight: 500;
  color: var(--text-muted);
}
.num {
  text-align: right;
}
.blocked {
  color: var(--warn);
}
.muted,
.empty {
  color: var(--text-muted);
}
.error {
  color: var(--fail);
}
</style>
