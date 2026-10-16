<script setup lang="ts">
import { computed } from 'vue'

const props = defineProps<{ status: string }>()

const tone = computed(() => {
  switch (props.status) {
    case 'completed':
    case 'passed':
    case 'ok':
      return 'ok'
    case 'failed':
    case 'error':
    case 'timeout':
    case 'max_steps':
      return 'fail'
    case 'running':
      return 'warn'
    default:
      return 'na'
  }
})
const icon = computed(() => ({ ok: '✓', fail: '✕', warn: '…', na: '–' })[tone.value])
</script>

<template>
  <span class="badge" :class="tone"><span aria-hidden="true">{{ icon }}</span>{{ status.replace('_', ' ') }}</span>
</template>

<style scoped>
.badge {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 1px 8px;
  border-radius: 999px;
  font-size: 12px;
  font-weight: 500;
  white-space: nowrap;
}
.ok {
  background: var(--ok-soft);
  color: var(--ok);
}
.fail {
  background: var(--fail-soft);
  color: var(--fail);
}
.warn {
  background: var(--warn-soft);
  color: var(--warn);
}
.na {
  background: var(--na-soft);
  color: var(--na);
}
</style>
