<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue'

// Shown only after a delay: fast local responses never flash a placeholder.
const props = withDefaults(defineProps<{ rows?: number; delay?: number }>(), {
  rows: 6,
  delay: 200,
})
const visible = ref(false)
let timer: ReturnType<typeof setTimeout> | undefined

onMounted(() => {
  timer = setTimeout(() => (visible.value = true), props.delay)
})
onBeforeUnmount(() => clearTimeout(timer))
</script>

<template>
  <div class="skeleton" aria-hidden="true">
    <template v-if="visible">
      <div v-for="i in rows" :key="i" class="bar" :class="`w${(i % 3) + 1}`"></div>
    </template>
  </div>
</template>

<style scoped>
.skeleton {
  display: grid;
  gap: 10px;
  padding: 12px 0;
}
.bar {
  height: 12px;
  border-radius: var(--radius-sm);
  background: var(--surface-2);
  animation: pulse 1.4s ease-in-out infinite;
}
.w1 {
  width: 92%;
}
.w2 {
  width: 76%;
}
.w3 {
  width: 84%;
}
@keyframes pulse {
  50% {
    opacity: 0.55;
  }
}
</style>
