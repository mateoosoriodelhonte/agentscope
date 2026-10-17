<script setup lang="ts">
import { nextTick, ref, useId, watch } from 'vue'

/*
 * A modal dialog, centred or as a drawer from the right. While open it traps Tab inside,
 * closes on Escape and on a backdrop click (unless `busy`), focuses the first control (or
 * the element marked data-autofocus), and returns focus to where it was when it closes.
 */
const props = withDefaults(
  defineProps<{
    open: boolean
    title: string
    variant?: 'dialog' | 'drawer'
    /** Blocks closing, e.g. while a request is in flight. */
    busy?: boolean
    width?: string
  }>(),
  { variant: 'dialog', busy: false, width: undefined },
)
const emit = defineEmits<{ close: [] }>()
const panel = ref<HTMLElement | null>(null)
const titleId = useId()
let returnFocus: HTMLElement | null = null

const FOCUSABLE =
  'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])'

function focusables(): HTMLElement[] {
  return [...(panel.value?.querySelectorAll<HTMLElement>(FOCUSABLE) ?? [])]
}

watch(
  () => props.open,
  async (open) => {
    if (open) {
      returnFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null
      await nextTick()
      const target = panel.value?.querySelector<HTMLElement>('[data-autofocus]') ?? focusables()[0] ?? panel.value
      target?.focus()
    } else {
      // Unless focus already moved on (e.g. into another dialog), put it back where it was.
      const active = document.activeElement
      if (!active || active === document.body || panel.value?.contains(active)) returnFocus?.focus()
      returnFocus = null
    }
  },
  { immediate: true },
)

function close(): void {
  if (!props.busy) emit('close')
}

function onKeydown(event: KeyboardEvent): void {
  if (event.key === 'Escape') {
    event.preventDefault()
    event.stopPropagation()
    close()
    return
  }
  if (event.key !== 'Tab') return
  const items = focusables()
  if (!items.length) {
    event.preventDefault()
    return
  }
  const first = items[0]!
  const last = items[items.length - 1]!
  if (event.shiftKey && document.activeElement === first) {
    event.preventDefault()
    last.focus()
  } else if (!event.shiftKey && document.activeElement === last) {
    event.preventDefault()
    first.focus()
  }
}
</script>

<template>
  <div v-if="open" class="backdrop" :class="variant" @click.self="close">
    <div
      ref="panel"
      class="modal"
      :class="variant"
      role="dialog"
      aria-modal="true"
      :aria-labelledby="titleId"
      :aria-busy="busy"
      tabindex="-1"
      :style="width ? { width } : undefined"
      @keydown="onKeydown"
    >
      <header class="modal-head">
        <h2 :id="titleId">{{ title }}</h2>
        <button type="button" class="btn ghost close" :disabled="busy" @click="close">Close</button>
      </header>
      <div class="modal-body"><slot /></div>
      <footer v-if="$slots.footer" class="modal-foot"><slot name="footer" /></footer>
    </div>
  </div>
</template>

<style scoped>
.backdrop {
  position: fixed;
  inset: 0;
  z-index: 30;
  display: grid;
  background: var(--overlay);
}
.backdrop.dialog {
  place-items: center;
}
.backdrop.drawer {
  justify-items: end;
}
.modal {
  display: flex;
  flex-direction: column;
  max-height: calc(100vh - 48px);
  border: 1px solid var(--border-strong);
  background: var(--surface-1);
  box-shadow: 0 12px 32px var(--shadow);
}
.modal:focus {
  outline: none;
}
.modal.dialog {
  width: min(560px, calc(100vw - 32px));
  border-radius: 8px;
}
.modal.drawer {
  width: min(620px, 100vw);
  height: 100vh;
  max-height: none;
  border-width: 0 0 0 1px;
}
.modal-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 12px 16px;
  border-bottom: 1px solid var(--border);
}
h2 {
  font-size: 15px;
}
.modal-body {
  flex: 1;
  min-height: 0;
  overflow: auto;
  padding: 14px 16px 16px;
}
.modal-foot {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: flex-end;
  gap: 8px;
  padding: 10px 16px;
  border-top: 1px solid var(--border);
}
</style>
