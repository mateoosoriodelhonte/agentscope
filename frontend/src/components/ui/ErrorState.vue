<script setup lang="ts">
import { computed } from 'vue'
import { describeError } from '@/api/errors'
import EmptyState from './EmptyState.vue'

/*
 * The error variant of EmptyState with the fix for the failure: start the API when nothing
 * answers, run the migration when the schema is behind, otherwise the server's message.
 */
const props = withDefaults(defineProps<{ error: unknown; title: string; compact?: boolean }>(), {
  compact: false,
})
const emit = defineEmits<{ retry: [] }>()
const described = computed(() => describeError(props.error))
const heading = computed(() => {
  if (described.value.kind === 'unreachable') return 'Cannot reach the AgentScope API'
  if (described.value.kind === 'migration') return 'Database migration pending'
  return props.title
})
</script>

<template>
  <EmptyState :title="heading" variant="error" :compact="compact">
    <template v-if="described.kind === 'unreachable'">
      <p>Nothing answered on this address. Start the server with <code>agentscope serve</code>, then retry.</p>
      <p class="detail">{{ described.message }}</p>
    </template>
    <template v-else-if="described.kind === 'migration'">
      <p>The database schema is older than this version of AgentScope. Upgrade it with <code>agentscope db upgrade</code>, then retry.</p>
      <p class="detail">{{ described.message }}</p>
    </template>
    <template v-else>
      <p>{{ described.message }}</p>
      <slot />
    </template>
    <template #actions>
      <button type="button" class="btn" @click="emit('retry')">Retry</button>
      <slot name="actions" />
    </template>
  </EmptyState>
</template>

<style scoped>
.detail {
  font-family: var(--font-mono);
  font-size: 11.5px;
  color: var(--text-faint);
}
</style>
