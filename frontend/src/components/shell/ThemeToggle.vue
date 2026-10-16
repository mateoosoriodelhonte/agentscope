<script setup lang="ts">
import Icon from '@/components/ui/Icon.vue'
import type { IconName } from '@/components/ui/icons'
import { useTheme, type ThemePreference } from '@/composables/useTheme'

const { preference, setPreference } = useTheme()
const OPTIONS: { value: ThemePreference; label: string; icon: IconName }[] = [
  { value: 'system', label: 'Match system theme', icon: 'monitor' },
  { value: 'light', label: 'Light theme', icon: 'sun' },
  { value: 'dark', label: 'Dark theme', icon: 'moon' },
]
</script>

<template>
  <div class="theme" role="group" aria-label="Theme">
    <button
      v-for="option in OPTIONS"
      :key="option.value"
      type="button"
      :aria-label="option.label"
      :title="option.label"
      :aria-pressed="preference === option.value"
      @click="setPreference(option.value)"
    >
      <Icon :name="option.icon" :size="14" />
    </button>
  </div>
</template>

<style scoped>
.theme {
  display: inline-flex;
  padding: 2px;
  border: 1px solid var(--border);
  border-radius: var(--radius);
  background: var(--surface-2);
}
button {
  display: grid;
  place-items: center;
  width: 26px;
  height: 22px;
  border: 0;
  border-radius: var(--radius-sm);
  background: none;
  color: var(--text-muted);
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
