<script lang="ts">
export async function copyText(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text)
    return true
  } catch {
    return false // no clipboard permission or insecure context
  }
}
</script>

<script setup lang="ts">
import { onBeforeUnmount, ref } from 'vue'
import Icon from './Icon.vue'

const props = defineProps<{ value: string; label: string }>()
const state = ref<'idle' | 'copied' | 'failed'>('idle')
let timer: ReturnType<typeof setTimeout> | undefined

async function copy(): Promise<void> {
  state.value = (await copyText(props.value)) ? 'copied' : 'failed'
  clearTimeout(timer)
  timer = setTimeout(() => (state.value = 'idle'), 1500)
}
onBeforeUnmount(() => clearTimeout(timer))
</script>

<template>
  <button type="button" class="copy" :aria-label="label" :title="label" @click="copy">
    <Icon :name="state === 'copied' ? 'check' : 'copy'" :size="13" />
    <span class="status" aria-live="polite">{{
      state === 'copied' ? 'Copied' : state === 'failed' ? 'Copy failed' : ''
    }}</span>
  </button>
</template>

<style scoped>
.copy {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 4px;
  /* WCAG 2.2 target size: at least 24 x 24 CSS px, even in dense tables. */
  min-width: 24px;
  height: 24px;
  padding: 0 5px;
  border: 0;
  border-radius: var(--radius-sm);
  background: none;
  color: var(--text-muted);
  cursor: pointer;
}
.copy:hover {
  background: var(--hover);
  color: var(--text);
}
.copy :deep(.icon) {
  vertical-align: 0;
}
.status {
  font-size: 11px;
}
.status:empty {
  display: none;
}
</style>
