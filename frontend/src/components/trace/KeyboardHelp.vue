<script setup lang="ts">
import { nextTick, ref, watch } from 'vue'
import Kbd from '@/components/ui/Kbd.vue'
import { keyLabel, TRACE_BINDINGS, type KeyBinding } from '@/composables/keybindings'

// Generated from the same registry the handlers use.
const props = defineProps<{ open: boolean }>()
const emit = defineEmits<{ close: [] }>()

const groups = TRACE_BINDINGS.reduce<Record<string, KeyBinding[]>>((acc, binding) => {
  ;(acc[binding.group] ??= []).push(binding)
  return acc
}, {})

const closeButton = ref<HTMLButtonElement | null>(null)
let returnFocus: HTMLElement | null = null

watch(
  () => props.open,
  async (open) => {
    if (open) {
      returnFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null
      await nextTick()
      closeButton.value?.focus()
    } else {
      returnFocus?.focus()
    }
  },
)

function onKeydown(event: KeyboardEvent): void {
  if (event.key === 'Escape' || event.key === '?') {
    event.preventDefault()
    emit('close')
  } else if (event.key === 'Tab') {
    event.preventDefault() // the close button is the only control: keep focus inside
  }
  event.stopPropagation()
}
</script>

<template>
  <div v-if="open" class="backdrop" @click.self="emit('close')">
    <div class="dialog" role="dialog" aria-modal="true" aria-labelledby="keys-title" @keydown="onKeydown">
      <header>
        <h2 id="keys-title">Keyboard shortcuts</h2>
        <button ref="closeButton" type="button" class="btn ghost" @click="emit('close')">Close</button>
      </header>
      <p class="muted">Active while focus is inside the trace.</p>
      <section v-for="(bindings, name) in groups" :key="name">
        <h3 class="section-title">{{ name }}</h3>
        <dl>
          <div v-for="binding in bindings" :key="binding.action" class="row">
            <dt>
              <template v-for="(key, i) in binding.keys" :key="key">
                <span v-if="i > 0" class="or">/</span><Kbd>{{ keyLabel(key) }}</Kbd>
              </template>
            </dt>
            <dd>{{ binding.description }}</dd>
          </div>
        </dl>
      </section>
    </div>
  </div>
</template>

<style scoped>
.backdrop {
  position: fixed;
  inset: 0;
  z-index: 20;
  display: grid;
  place-items: center;
  background: var(--overlay);
}
.dialog {
  width: min(520px, calc(100vw - 32px));
  max-height: calc(100vh - 64px);
  overflow: auto;
  padding: 16px 20px 18px;
  border: 1px solid var(--border-strong);
  border-radius: 8px;
  background: var(--surface-1);
  box-shadow: 0 12px 32px var(--shadow);
}
header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
h2 {
  font-size: 15px;
}
.muted {
  margin: 2px 0 6px;
  font-size: 12.5px;
}
section {
  margin-top: 12px;
}
dl {
  margin: 6px 0 0;
}
.row {
  display: grid;
  grid-template-columns: 150px minmax(0, 1fr);
  gap: 12px;
  padding: 3px 0;
  font-size: 13px;
}
dt {
  display: flex;
  align-items: center;
  gap: 4px;
}
dd {
  margin: 0;
  color: var(--text-muted);
}
.or {
  color: var(--text-faint);
  font-size: 11px;
}
</style>
