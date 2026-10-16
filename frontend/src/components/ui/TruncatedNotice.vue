<script setup lang="ts">
import Icon from './Icon.vue'

withDefaults(defineProps<{ detail?: string }>(), {
  detail:
    'Strings over 8 KiB were shortened, and payloads over 64 KiB keep only a preview. What you see is what was stored.',
})
</script>

<template>
  <p class="truncated" role="note">
    <Icon name="scissors" :size="14" />
    <span><strong>Payload truncated when recorded.</strong> {{ detail }}</span>
  </p>
</template>

<style scoped>
.truncated {
  display: flex;
  gap: 8px;
  align-items: flex-start;
  margin: 0 0 8px;
  padding: 6px 10px;
  border: 1px solid var(--border);
  border-left: 3px solid var(--warn);
  border-radius: var(--radius);
  background: var(--warn-soft);
  color: var(--text);
  font-size: 12.5px;
}
.truncated :deep(.icon) {
  color: var(--warn);
  margin-top: 2px;
}
</style>
