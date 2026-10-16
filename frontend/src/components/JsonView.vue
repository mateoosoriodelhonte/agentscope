<script setup lang="ts">
import { computed } from 'vue'
import type { JsonValue } from '@/api/types'

// Untrusted payloads are rendered as text only (never v-html).
const props = defineProps<{ value: JsonValue | undefined }>()
const text = computed(() => (props.value === undefined ? '' : JSON.stringify(props.value, null, 2)))
</script>

<template>
  <pre class="json mono">{{ text }}</pre>
</template>

<style scoped>
.json {
  margin: 0;
  padding: 10px 12px;
  background: var(--surface-2);
  border: 1px solid var(--border);
  border-radius: 6px;
  overflow: auto;
  max-height: 360px;
  white-space: pre-wrap;
  word-break: break-word;
}
</style>
