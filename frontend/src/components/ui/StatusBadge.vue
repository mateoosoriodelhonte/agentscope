<script setup lang="ts">
import { computed } from 'vue'
import type { Outcome } from '@/composables/useTraceModel'
import Icon from './Icon.vue'
import { statusOutcome, TONE_ICONS } from './status'

// Status is always icon plus text, never colour alone.
const props = withDefaults(
  defineProps<{ status?: string; outcome?: Outcome; variant?: 'soft' | 'plain' }>(),
  { status: undefined, outcome: undefined, variant: 'soft' },
)
const resolved = computed<Outcome>(() => props.outcome ?? statusOutcome(props.status ?? 'unknown'))
</script>

<template>
  <span class="badge" :class="[resolved.tone, variant]">
    <Icon :name="TONE_ICONS[resolved.tone]" :size="12" />
    <span class="label">{{ resolved.label }}</span>
  </span>
</template>

<style scoped>
.badge {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  max-width: 100%;
  font-size: 12px;
  font-weight: 500;
  line-height: 18px;
  white-space: nowrap;
}
.soft {
  padding: 0 7px 0 5px;
  border-radius: 999px;
}
.badge :deep(.icon) {
  vertical-align: 0;
}
.label {
  overflow: hidden;
  text-overflow: ellipsis;
}
.ok {
  color: var(--ok);
}
.fail {
  color: var(--fail);
}
.warn {
  color: var(--warn);
}
.na {
  color: var(--na);
}
.neutral {
  color: var(--neutral);
}
.soft.ok {
  background: var(--ok-soft);
}
.soft.fail {
  background: var(--fail-soft);
}
.soft.warn {
  background: var(--warn-soft);
}
.soft.na {
  background: var(--na-soft);
}
.soft.neutral {
  background: var(--neutral-soft);
}
</style>
