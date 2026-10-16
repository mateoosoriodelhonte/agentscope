<script setup lang="ts">
/*
 * One labelled value. A missing value is said out loud ("not reported", "not configured")
 * instead of being shown as 0, so a metric is never invented. Render inside a <dl>.
 */
withDefaults(
  defineProps<{
    label: string
    value: string | null
    missing?: 'not reported' | 'not configured'
    hint?: string
  }>(),
  { missing: 'not reported', hint: undefined },
)
</script>

<template>
  <div class="metric" :title="hint">
    <dt>{{ label }}</dt>
    <dd v-if="value !== null" class="value mono">{{ value }}</dd>
    <dd v-else class="missing">{{ missing }}</dd>
  </div>
</template>

<style scoped>
.metric {
  display: flex;
  flex-direction: column;
  gap: 1px;
  min-width: 0;
}
dt {
  font-size: 11px;
  color: var(--text-muted);
}
dd {
  margin: 0;
  white-space: nowrap;
}
.value {
  font-size: 13px;
  color: var(--text);
}
.missing {
  font-size: 12.5px;
  font-style: italic;
  color: var(--text-faint);
}
</style>
