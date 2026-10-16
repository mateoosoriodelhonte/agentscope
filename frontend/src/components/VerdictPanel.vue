<script setup lang="ts">
import { computed } from 'vue'
import type { CaseResult } from '@/api/types'
import StatusBadge from './StatusBadge.vue'

const props = defineProps<{ evaluation: CaseResult | null }>()
const emit = defineEmits<{ select: [spanId: string] }>()

const order = { failed: 0, passed: 1, not_applicable: 2 } as const
const checks = computed(() =>
  [...(props.evaluation?.checks ?? [])].sort((a, b) => order[a.status] - order[b.status]),
)
</script>

<template>
  <section class="verdict" aria-labelledby="verdict-title">
    <header>
      <h2 id="verdict-title">Evaluation</h2>
      <StatusBadge v-if="evaluation" :status="evaluation.passed ? 'passed' : 'failed'" />
    </header>
    <p v-if="!evaluation" class="muted">This run was not evaluated against a test case.</p>
    <ul v-else class="checks">
      <li v-for="check in checks" :key="check.name" :class="check.status">
        <div class="line">
          <StatusBadge :status="check.status" />
          <span class="mono">{{ check.name }}</span>
        </div>
        <p class="message">{{ check.message }}</p>
        <button
          v-for="(ev, i) in check.evidence.filter((e) => e.span_id)"
          :key="i"
          type="button"
          class="evidence"
          @click="emit('select', ev.span_id!)"
        >
          {{ ev.role }}: {{ ev.detail }}
        </button>
      </li>
    </ul>
  </section>
</template>

<style scoped>
.verdict header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 8px;
}
h2 {
  font-size: 13px;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  color: var(--text-muted);
}
.muted {
  color: var(--text-muted);
}
.checks {
  list-style: none;
  margin: 0;
  padding: 0;
  display: grid;
  gap: 8px;
}
.checks li {
  padding: 8px 10px;
  border: 1px solid var(--border);
  border-radius: 6px;
  background: var(--surface-1);
}
.checks li.failed {
  border-color: var(--fail);
}
.line {
  display: flex;
  gap: 8px;
  align-items: center;
}
.message {
  margin: 4px 0 0;
  color: var(--text-muted);
}
.evidence {
  margin: 6px 6px 0 0;
  padding: 2px 8px;
  border: 1px solid var(--border);
  border-radius: 4px;
  background: var(--surface-2);
  color: var(--text);
  font: inherit;
  font-size: 12px;
  cursor: pointer;
}
</style>
