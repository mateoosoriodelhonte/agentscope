<script lang="ts">
export interface TabItem {
  id: string
  label: string
}
</script>

<script setup lang="ts">
import { nextTick, useId } from 'vue'

// WAI-ARIA tabs with automatic activation: arrows, Home and End move and select.
const props = defineProps<{ tabs: TabItem[]; label: string }>()
const active = defineModel<string>({ required: true })
const base = useId()
const tabRefs: HTMLButtonElement[] = []

function select(index: number): void {
  const tab = props.tabs[(index + props.tabs.length) % props.tabs.length]
  if (!tab) return
  active.value = tab.id
  void nextTick(() => tabRefs[props.tabs.indexOf(tab)]?.focus())
}

function onKeydown(event: KeyboardEvent, index: number): void {
  const moves: Record<string, number> = {
    ArrowRight: index + 1,
    ArrowLeft: index - 1,
    Home: 0,
    End: props.tabs.length - 1,
  }
  const target = moves[event.key]
  if (target === undefined) return
  event.preventDefault()
  event.stopPropagation()
  select(target)
}
</script>

<template>
  <div class="tabs">
    <div class="tablist" role="tablist" :aria-label="label">
      <button
        v-for="(tab, i) in tabs"
        :id="`${base}-tab-${tab.id}`"
        :key="tab.id"
        :ref="(el) => (tabRefs[i] = el as HTMLButtonElement)"
        type="button"
        role="tab"
        class="tab"
        :aria-selected="tab.id === active"
        :aria-controls="`${base}-panel`"
        :tabindex="tab.id === active ? 0 : -1"
        @click="active = tab.id"
        @keydown="onKeydown($event, i)"
      >
        {{ tab.label }}
      </button>
    </div>
    <div
      :id="`${base}-panel`"
      class="panel-body"
      role="tabpanel"
      tabindex="0"
      :aria-labelledby="`${base}-tab-${active}`"
    >
      <slot :active="active" />
    </div>
  </div>
</template>

<style scoped>
.tablist {
  display: flex;
  gap: 2px;
  border-bottom: 1px solid var(--border);
}
.tab {
  position: relative;
  padding: 6px 10px;
  border: 0;
  background: none;
  color: var(--text-muted);
  font-size: 13px;
  cursor: pointer;
}
.tab:hover {
  color: var(--text);
}
.tab[aria-selected='true'] {
  color: var(--text);
  font-weight: 500;
}
.tab[aria-selected='true']::after {
  content: '';
  position: absolute;
  left: 6px;
  right: 6px;
  bottom: -1px;
  height: 2px;
  border-radius: 1px;
  background: var(--accent);
}
.panel-body {
  padding-top: 12px;
}
.panel-body:focus-visible {
  outline-offset: 4px;
}
</style>
