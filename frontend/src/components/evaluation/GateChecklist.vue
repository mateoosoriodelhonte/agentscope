<script setup lang="ts">
import type { GateResult } from '@/api/types'
import StatusBadge from '@/components/ui/StatusBadge.vue'
import { formatActual, formatThreshold, gateOutcome, ruleLabel } from './gate'

// One row per gate rule: what was required, what was measured, and whether it held.
withDefaults(defineProps<{ gate: GateResult | null; heading?: string }>(), { heading: 'Gate' })
</script>

<template>
  <section class="gate" aria-label="Gate checklist">
    <div class="section-head">
      <h2 class="section-title">{{ heading }}</h2>
      <StatusBadge :outcome="gateOutcome(gate?.passed)" />
      <span v-if="gate" class="faint baseline">
        {{ gate.baseline_used ? 'compared with a baseline' : 'no baseline: regression rules cannot be measured' }}
      </span>
    </div>
    <p v-if="!gate" class="faint none">No gate thresholds were configured for this evaluation.</p>
    <p v-else-if="!gate.rules.length" class="faint none">The gate has no rules.</p>
    <div v-else class="table-wrap panel">
      <table class="data-table">
        <thead>
          <tr>
            <th scope="col">Rule</th>
            <th scope="col">Threshold</th>
            <th scope="col">Actual</th>
            <th scope="col">Result</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="rule in gate.rules" :key="rule.name" :class="{ failed: !rule.passed }">
            <th scope="row">
              <span class="label">{{ ruleLabel(rule) }}</span>
              <span v-if="rule.detail" class="detail">{{ rule.detail }}</span>
            </th>
            <td class="mono">{{ formatThreshold(rule) }}</td>
            <td class="mono">{{ formatActual(rule) }}</td>
            <td><StatusBadge :status="rule.passed ? 'passed' : 'failed'" /></td>
          </tr>
        </tbody>
      </table>
    </div>
  </section>
</template>

<style scoped>
.gate {
  display: grid;
  gap: 8px;
}
.baseline {
  font-size: 12px;
}
.none {
  margin: 0;
  font-size: 13px;
}
tr.failed th {
  box-shadow: inset 3px 0 0 var(--fail);
}
.label {
  display: block;
}
.detail {
  display: block;
  font-size: 11.5px;
  font-weight: 400;
  color: var(--text-muted);
  overflow-wrap: anywhere;
}
td.mono {
  overflow-wrap: anywhere;
}
</style>
