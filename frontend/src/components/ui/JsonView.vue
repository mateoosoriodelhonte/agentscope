<script setup lang="ts">
import { computed } from 'vue'
import type { JsonValue } from '@/api/types'
import { jsonLines } from './jsonLines'
import RedactedValue from './RedactedValue.vue'

// Untrusted payloads are rendered as text nodes only: never v-html, never innerHTML.
const props = withDefaults(defineProps<{ value: JsonValue; label?: string }>(), {
  label: 'JSON value',
})
const lines = computed(() => jsonLines(props.value))
</script>

<template>
  <pre class="json" tabindex="0" :aria-label="label"><span
    v-for="(line, i) in lines"
    :key="i"
    class="line"
  >{{ '  '.repeat(line.depth) }}<template v-for="(token, j) in line.tokens" :key="j"><RedactedValue
    v-if="token.kind === 'redacted'"
    :marker="token.text"
  /><span v-else :class="token.kind">{{ token.text }}</span></template>{{ '\n' }}</span></pre>
</template>

<style scoped>
.json {
  margin: 0;
  padding: 8px 10px;
  background: var(--surface-2);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  overflow: auto;
  max-height: 420px;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  line-height: 1.5;
  color: var(--text);
}
.key {
  color: var(--neutral);
}
.string {
  color: var(--text);
}
.number,
.boolean {
  color: var(--ok);
}
.null,
.punct {
  color: var(--text-muted);
}
.truncated {
  color: var(--warn);
  font-style: italic;
}
</style>
