<script setup lang="ts">
import Icon from './Icon.vue'

// Shown on everything produced by the deterministic fake provider (agentscope.fake).
withDefaults(defineProps<{ compact?: boolean }>(), { compact: false })
const TEXT = 'Simulated model: demonstrates harness mechanics, not model behaviour'
</script>

<template>
  <span class="simulated" :class="{ compact }" :title="TEXT">
    <Icon name="flask" :size="13" />
    <span v-if="compact">simulated</span>
    <span v-else>{{ TEXT }}</span>
  </span>
</template>

<style scoped>
.simulated {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 1px 8px 1px 6px;
  border: 1px solid var(--border-strong);
  border-radius: 999px;
  background: var(--surface-2);
  color: var(--text-muted);
  font-size: 12px;
  white-space: nowrap;
}
.simulated.compact {
  padding: 0 6px 0 4px;
  font-size: 11px;
}
.simulated :deep(.icon) {
  vertical-align: 0;
}
</style>
