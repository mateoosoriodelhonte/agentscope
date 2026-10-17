<script setup lang="ts">
// Every chart has a table equivalent; this switches between the two.
defineProps<{ label: string }>()
const view = defineModel<'chart' | 'table'>({ required: true })
</script>

<template>
  <div class="toggle" role="group" :aria-label="label">
    <button type="button" :aria-pressed="view === 'chart'" @click="view = 'chart'">Chart</button>
    <button type="button" :aria-pressed="view === 'table'" @click="view = 'table'">Table</button>
  </div>
</template>

<style scoped>
.toggle {
  display: inline-flex;
  padding: 2px;
  border: 1px solid var(--border);
  border-radius: var(--radius);
  background: var(--surface-2);
}
button {
  height: 22px;
  padding: 0 9px;
  border: 0;
  border-radius: var(--radius-sm);
  background: none;
  color: var(--text-muted);
  font-size: 12px;
  cursor: pointer;
}
button:hover {
  color: var(--text);
}
button[aria-pressed='true'] {
  background: var(--surface-1);
  color: var(--text);
  box-shadow: 0 0 0 1px var(--border);
}
</style>
