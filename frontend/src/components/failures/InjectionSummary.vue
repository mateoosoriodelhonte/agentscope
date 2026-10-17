<script setup lang="ts">
import type { InjectionSummary } from '@/api/types'
import SimulatedBadge from '@/components/ui/SimulatedBadge.vue'
import StatusBadge from '@/components/ui/StatusBadge.vue'

/*
 * Prompt-injection outcomes per agent version. Attempts measure what the model tried (under
 * the simulated model, a scripted susceptibility); executions are what the runtime let
 * through, which its permissions must hold at 0.
 */
defineProps<{ items: InjectionSummary[] }>()
</script>

<template>
  <ul class="injection">
    <li v-for="item in items" :key="item.agent_ref" class="panel card">
      <div class="head">
        <span class="mono ref">{{ item.agent_ref }}</span>
        <SimulatedBadge v-if="item.deterministic" compact />
        <span class="faint cases">{{ item.cases }} injection cases</span>
      </div>
      <dl>
        <div>
          <dt>{{ item.deterministic ? 'Unauthorized attempts (simulated susceptibility)' : 'Unauthorized attempts' }}</dt>
          <dd class="mono">{{ item.unauthorized_attempts }}</dd>
        </div>
        <div :class="{ bad: item.unauthorized_executions > 0 }">
          <dt>Unauthorized executions (enforced by runtime permissions, must be 0)</dt>
          <dd>
            <span class="mono">{{ item.unauthorized_executions }}</span>
            <StatusBadge
              :outcome="item.unauthorized_executions === 0 ? { tone: 'ok', label: 'contained' } : { tone: 'fail', label: 'not contained' }"
              variant="plain"
            />
          </dd>
        </div>
        <div :class="{ bad: item.leaked_cases > 0 }">
          <dt>Cases that leaked protected content</dt>
          <dd class="mono">{{ item.leaked_cases }}</dd>
        </div>
      </dl>
    </li>
  </ul>
</template>

<style scoped>
.injection {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
  gap: 10px;
  margin: 0;
  padding: 0;
  list-style: none;
}
.card {
  padding: 10px 12px;
}
.head {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  margin-bottom: 8px;
}
.ref {
  color: var(--text);
  font-size: 13px;
}
.cases {
  margin-left: auto;
  font-size: 12px;
}
dl {
  display: grid;
  gap: 6px;
  margin: 0;
}
dt {
  font-size: 11.5px;
  color: var(--text-muted);
}
dd {
  display: flex;
  align-items: center;
  gap: 8px;
  margin: 0;
  font-size: 13px;
}
.bad dd .mono {
  color: var(--fail);
  font-weight: 600;
}
</style>
