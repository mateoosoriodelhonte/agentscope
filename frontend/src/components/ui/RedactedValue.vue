<script setup lang="ts">
import { computed } from 'vue'
import Icon from './Icon.vue'

/** A redaction marker (`[REDACTED]` or `[REDACTED:kind]`) recorded in place of a secret. */
const props = defineProps<{ marker: string }>()
const kind = computed(() => /^\[REDACTED:([a-z_]+)\]$/.exec(props.marker)?.[1] ?? null)
const title = computed(
  () =>
    `Redacted when the trace was recorded${kind.value ? ` (${kind.value.replace(/_/g, ' ')})` : ''}. The original value was never stored.`,
)
</script>

<template>
  <span class="redacted" :title="title"><Icon name="lock" :size="11" />{{ marker }}</span>
</template>

<style scoped>
.redacted {
  display: inline-flex;
  align-items: center;
  gap: 3px;
  padding: 0 4px;
  border-radius: var(--radius-sm);
  background: var(--redacted-soft);
  color: var(--redacted);
  font-family: var(--font-mono);
  font-size: 11.5px;
  white-space: nowrap;
}
.redacted :deep(.icon) {
  vertical-align: 0;
}
</style>
