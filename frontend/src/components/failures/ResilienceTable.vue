<script setup lang="ts">
import { computed, useId } from 'vue'
import type { ResilienceRow } from '@/api/types'
import SimulatedBadge from '@/components/ui/SimulatedBadge.vue'
import { ratio } from '@/composables/format'

/*
 * How each agent version handled each injected fault kind: recovered (succeeded after the
 * fault), degraded (gave up gracefully), failed. Rows are fault kinds, column groups are
 * agent versions, and the footer sums each column. Agent versions whose counts come from
 * the simulated model carry its badge: scripted recoveries say nothing about a real model.
 */
const props = withDefaults(defineProps<{ rows: ResilienceRow[]; simulated?: ReadonlySet<string> }>(), {
  simulated: () => new Set<string>(),
})
const captionId = useId()

const agents = computed(() => [...new Set(props.rows.map((r) => r.agent_ref))].sort())
const faults = computed(() => [...new Set(props.rows.map((r) => r.fault_kind))].sort())
const cell = (fault: string, agent: string) => props.rows.find((r) => r.fault_kind === fault && r.agent_ref === agent) ?? null
const totals = computed(() =>
  agents.value.map((agent) =>
    props.rows
      .filter((r) => r.agent_ref === agent)
      .reduce(
        (sum, r) => ({
          recovered: sum.recovered + r.recovered,
          degraded: sum.degraded + r.degraded,
          failed: sum.failed + r.failed,
          passed: sum.passed + r.passed,
          cases: sum.cases + r.cases,
        }),
        { recovered: 0, degraded: 0, failed: 0, passed: 0, cases: 0 },
      ),
  ),
)
</script>

<template>
  <!-- Wider than the page with several agent versions: a focusable, named region, so the
       horizontal scroll is reachable from the keyboard too. -->
  <div class="table-wrap panel" role="region" tabindex="0" :aria-labelledby="captionId">
    <table class="data-table resilience">
      <caption :id="captionId" class="visually-hidden">Outcomes by fault kind and agent version</caption>
      <thead>
        <tr>
          <th scope="col" rowspan="2">Fault kind</th>
          <th v-for="agent in agents" :key="agent" scope="colgroup" colspan="4" class="agent">
            <span class="ref mono">{{ agent }}</span>
            <SimulatedBadge v-if="simulated.has(agent)" compact class="badge" />
          </th>
        </tr>
        <tr>
          <template v-for="agent in agents" :key="agent">
            <th scope="col" class="num first">Recovered</th>
            <th scope="col" class="num">Degraded</th>
            <th scope="col" class="num">Failed</th>
            <th scope="col" class="num">Passed</th>
          </template>
        </tr>
      </thead>
      <tbody>
        <tr v-for="fault in faults" :key="fault">
          <th scope="row" class="mono">{{ fault }}</th>
          <template v-for="agent in agents" :key="agent">
            <template v-if="cell(fault, agent)">
              <td class="num mono first">{{ cell(fault, agent)!.recovered }}</td>
              <td class="num mono">{{ cell(fault, agent)!.degraded }}</td>
              <td class="num mono" :class="{ bad: cell(fault, agent)!.failed > 0 }">{{ cell(fault, agent)!.failed }}</td>
              <td class="num mono">{{ ratio(cell(fault, agent)!.passed, cell(fault, agent)!.cases) }}</td>
            </template>
            <td v-else colspan="4" class="none first">not tested</td>
          </template>
        </tr>
      </tbody>
      <tfoot>
        <tr>
          <th scope="row">Total over fault kinds</th>
          <template v-for="(total, i) in totals" :key="agents[i]">
            <td class="num mono first">{{ total.recovered }}</td>
            <td class="num mono">{{ total.degraded }}</td>
            <td class="num mono" :class="{ bad: total.failed > 0 }">{{ total.failed }}</td>
            <td class="num mono">{{ ratio(total.passed, total.cases) }}</td>
          </template>
        </tr>
      </tfoot>
    </table>
  </div>
  <p class="faint note">A case with several fault kinds is counted once under each kind, so totals count case and fault-kind pairs.</p>
</template>

<style scoped>
.agent {
  text-align: center;
  border-left: 1px solid var(--border);
}
.badge {
  margin-left: 6px;
}
.first {
  border-left: 1px solid var(--border);
}
.bad {
  color: var(--fail);
  font-weight: 600;
}
.none {
  text-align: center;
  font-size: 12px;
  color: var(--text-faint);
}
.note {
  margin: 0;
  font-size: 12px;
}
</style>
