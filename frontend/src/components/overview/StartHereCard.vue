<script setup lang="ts">
import { computed } from 'vue'
import { RouterLink } from 'vue-router'
import type { SuiteListItem } from '@/api/types'

/*
 * The demo story in five steps: a gate fails, Compare shows why, the regressed case leads
 * to a TraceDiff, whose first divergence leads to the evidence span. Steps link to the real
 * data once it exists (after "Load demo data", or the user's own evaluations).
 */
const props = defineProps<{ suites: SuiteListItem[]; seeding: boolean; progress: string | null; error: string | null }>()
const emit = defineEmits<{ dismiss: []; seed: [] }>()

const failing = computed(() => props.suites.find((s) => s.status === 'completed' && s.gate_passed === false) ?? null)
/** The newest other agent version evaluated on the same dataset content, preferring a passing one. */
const counterpart = computed(() => {
  const f = failing.value
  if (!f) return null
  const candidates = props.suites.filter(
    (s) => s.status === 'completed' && s.dataset_hash === f.dataset_hash && s.agent_ref !== f.agent_ref,
  )
  return candidates.find((s) => s.gate_passed === true) ?? candidates[0] ?? null
})
const failingLabel = computed(() => {
  const f = failing.value
  return f ? `Open ${f.agent_ref} on ${f.dataset_name}@${f.dataset_version}` : ''
})
const compareLink = computed(() =>
  failing.value && counterpart.value
    ? { path: '/compare', query: { a: failing.value.suite_id, b: counterpart.value.suite_id } }
    : '/compare',
)
</script>

<template>
  <section class="start panel" aria-labelledby="start-title">
    <header class="head">
      <h2 id="start-title">Start here</h2>
      <button type="button" class="btn ghost" @click="emit('dismiss')">Dismiss</button>
    </header>
    <p class="intro">Follow one regression from the gate that caught it to the span that explains it.</p>
    <ol class="steps">
      <li>
        <strong>A gate fails.</strong>
        <!-- Link text on one line: surrounding whitespace would be underlined too. -->
        <RouterLink v-if="failing" :to="`/evaluations/${encodeURIComponent(failing.suite_id)}`">{{ failingLabel }}</RouterLink>
        <span v-else class="muted">Evaluate a dataset with gate thresholds, or load the demo.</span>
      </li>
      <li>
        <strong>Compare it with another version.</strong>
        <RouterLink :to="compareLink">{{ counterpart ? `Compare with ${counterpart.agent_ref}` : 'Open Compare' }}</RouterLink>
      </li>
      <li><strong>Open the regressed case.</strong> <span class="muted">The flip list puts regressions first.</span></li>
      <li>
        <strong>Find the first divergence.</strong>
        <span class="muted">The trace diff aligns both runs step by step and names where they part.</span>
      </li>
      <li>
        <strong>Inspect the evidence span.</strong>
        <span class="muted">Open the failed check: the trace selects and outlines the span behind it.</span>
      </li>
    </ol>
    <div class="actions">
      <button type="button" class="btn primary" :disabled="seeding" @click="emit('seed')">
        {{ seeding ? 'Loading demo data…' : 'Load demo data' }}
      </button>
      <span v-if="progress" class="muted" role="status">{{ progress }}</span>
      <span v-if="error" class="error" role="alert">{{ error }}</span>
    </div>
  </section>
</template>

<style scoped>
.start {
  display: grid;
  gap: 8px;
  padding: 14px 16px;
  border-left: 3px solid var(--accent);
}
.head {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
h2 {
  font-size: 15px;
}
.intro {
  margin: 0;
  color: var(--text-muted);
  font-size: 13px;
}
.steps {
  display: grid;
  gap: 4px;
  margin: 0;
  padding-left: 20px;
  font-size: 13px;
}
.steps strong {
  font-weight: 600;
  margin-right: 4px;
}
/* Links inside the step sentences need more than colour to stand out (WCAG 1.4.1). */
.steps a {
  text-decoration: underline;
  text-underline-offset: 2px;
}
.actions {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 10px;
  margin-top: 4px;
  font-size: 12.5px;
}
.error {
  color: var(--fail);
}
</style>
