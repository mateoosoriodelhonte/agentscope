<script setup lang="ts">
import { computed } from 'vue'
import Icon from './Icon.vue'
import type { IconName } from './icons'

/*
 * Honest empty states: a first run (nothing exists yet), a filter that matched nothing, an
 * error with the fix, or a screen that ships later.
 */
const props = withDefaults(
  defineProps<{
    title: string
    variant?: 'first-run' | 'filtered' | 'error' | 'placeholder'
    compact?: boolean
  }>(),
  { variant: 'first-run', compact: false },
)
const ICONS: Record<NonNullable<typeof props.variant>, IconName> = {
  'first-run': 'play',
  filtered: 'filter',
  error: 'alert',
  placeholder: 'overview',
}
const icon = computed(() => ICONS[props.variant])
</script>

<template>
  <section class="empty" :class="[variant, { compact }]" :role="variant === 'error' ? 'alert' : undefined">
    <span class="glyph"><Icon :name="icon" :size="compact ? 16 : 20" /></span>
    <div class="body">
      <h2 class="title">{{ title }}</h2>
      <div class="text"><slot /></div>
      <div v-if="$slots.actions" class="actions"><slot name="actions" /></div>
    </div>
  </section>
</template>

<style scoped>
.empty {
  display: flex;
  gap: 14px;
  align-items: flex-start;
  max-width: 640px;
  padding: 28px 24px;
}
.empty.compact {
  padding: 12px 4px;
  gap: 10px;
}
.glyph {
  display: grid;
  place-items: center;
  flex: none;
  width: 36px;
  height: 36px;
  border-radius: 50%;
  background: var(--surface-2);
  color: var(--text-muted);
}
.compact .glyph {
  width: 28px;
  height: 28px;
}
.error .glyph {
  background: var(--fail-soft);
  color: var(--fail);
}
.title {
  font-size: 14px;
  margin: 6px 0 4px;
}
.compact .title {
  font-size: 13px;
  margin-top: 4px;
}
.text {
  color: var(--text-muted);
  font-size: 13px;
}
.text :deep(p) {
  margin: 0 0 8px;
}
.text :deep(code) {
  padding: 1px 4px;
  border-radius: var(--radius-sm);
  background: var(--surface-2);
  color: var(--text);
}
.actions {
  display: flex;
  gap: 8px;
  margin-top: 12px;
}
</style>
