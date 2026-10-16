<script setup lang="ts">
import { computed, ref, useId } from 'vue'

/*
 * Two resizable panes. The separator follows the WAI-ARIA window-splitter pattern: drag it,
 * or focus it and use the arrow keys, Home and End. Below 1100px the panes stack. The ratio
 * is remembered per browser (a convenience only; storage failures are ignored).
 */
const props = withDefaults(
  defineProps<{ label: string; storageKey?: string; initial?: number; min?: number; max?: number }>(),
  { storageKey: undefined, initial: 0.6, min: 0.3, max: 0.75 },
)

const clamp = (value: number) => Math.min(props.max, Math.max(props.min, value))

function load(): number {
  if (!props.storageKey) return props.initial
  try {
    const stored = Number(window.localStorage.getItem(props.storageKey))
    return stored ? clamp(stored) : props.initial
  } catch {
    return props.initial
  }
}

function save(): void {
  if (!props.storageKey) return
  try {
    window.localStorage.setItem(props.storageKey, ratio.value.toFixed(3))
  } catch {
    /* storage blocked */
  }
}

const ratio = ref(load())
const root = ref<HTMLElement | null>(null)
const firstId = useId()
const dragging = ref(false)
const percent = computed(() => Math.round(ratio.value * 100))

function onPointerDown(event: PointerEvent): void {
  dragging.value = true
  ;(event.currentTarget as HTMLElement).setPointerCapture?.(event.pointerId)
}

function onPointerMove(event: PointerEvent): void {
  if (!dragging.value || !root.value) return
  const rect = root.value.getBoundingClientRect()
  if (rect.width > 0) ratio.value = clamp((event.clientX - rect.left) / rect.width)
}

function onPointerUp(): void {
  if (!dragging.value) return
  dragging.value = false
  save()
}

function onKeydown(event: KeyboardEvent): void {
  const next: Record<string, number> = {
    ArrowLeft: ratio.value - 0.02,
    ArrowRight: ratio.value + 0.02,
    Home: props.min,
    End: props.max,
  }
  const value = next[event.key]
  if (value === undefined) return
  event.preventDefault()
  event.stopPropagation()
  ratio.value = clamp(value)
  save()
}
</script>

<template>
  <div ref="root" class="split" :class="{ dragging }" :style="{ '--split': `${ratio * 100}%` }">
    <div :id="firstId" class="pane first"><slot name="first" /></div>
    <div
      class="separator"
      role="separator"
      tabindex="0"
      aria-orientation="vertical"
      :aria-label="label"
      :aria-controls="firstId"
      :aria-valuenow="percent"
      :aria-valuemin="Math.round(min * 100)"
      :aria-valuemax="Math.round(max * 100)"
      @pointerdown="onPointerDown"
      @pointermove="onPointerMove"
      @pointerup="onPointerUp"
      @pointercancel="onPointerUp"
      @keydown="onKeydown"
    ></div>
    <div class="pane second"><slot name="second" /></div>
  </div>
</template>

<style scoped>
.split {
  display: grid;
  grid-template-columns: minmax(0, var(--split)) 9px minmax(0, 1fr);
  min-height: 0;
  height: 100%;
}
.pane {
  min-width: 0;
  min-height: 0;
  overflow: auto;
}
.separator {
  position: relative;
  cursor: col-resize;
  touch-action: none;
}
.separator::before {
  content: '';
  position: absolute;
  top: 0;
  bottom: 0;
  left: 4px;
  width: 1px;
  background: var(--border);
}
.separator:hover::before,
.separator:focus-visible::before,
.dragging .separator::before {
  left: 3px;
  width: 3px;
  background: var(--accent);
}
.separator:focus-visible {
  outline: none;
}
.dragging {
  cursor: col-resize;
  user-select: none;
}
@media (max-width: 1099px) {
  .split {
    grid-template-columns: minmax(0, 1fr);
    height: auto;
  }
  .separator {
    display: none;
  }
  .pane {
    overflow: visible;
  }
}
</style>
