<script setup lang="ts">
import Kbd from '@/components/ui/Kbd.vue'
import type { BarMode } from './DurationBar.vue'

defineProps<{ mode: BarMode }>()
const emit = defineEmits<{ 'update:mode': [mode: BarMode] }>()
const OPTIONS: { value: BarMode; label: string; title: string }[] = [
  { value: 'relative', label: 'Relative', title: 'Bars start at zero, scaled to the longest span' },
  { value: 'timeline', label: 'Timeline', title: 'Bars placed on the run’s real time axis' },
]
</script>

<template>
  <div class="mode" role="group" aria-label="Duration bars">
    <button
      v-for="option in OPTIONS"
      :key="option.value"
      type="button"
      :title="option.title"
      :aria-pressed="mode === option.value"
      @click="emit('update:mode', option.value)"
    >
      {{ option.label }}
    </button>
    <Kbd class="hint">t</Kbd>
  </div>
</template>

<style scoped>
.mode {
  display: inline-flex;
  align-items: center;
  gap: 2px;
  padding: 2px;
  border: 1px solid var(--border);
  border-radius: var(--radius);
  background: var(--surface-2);
}
button {
  height: 22px;
  padding: 0 8px;
  border: 0;
  border-radius: var(--radius-sm);
  background: none;
  color: var(--text-muted);
  font-size: 12px;
  cursor: pointer;
}
button[aria-pressed='true'] {
  background: var(--surface-1);
  color: var(--text);
  box-shadow: 0 0 0 1px var(--border);
}
.hint {
  margin: 0 4px;
}
</style>
