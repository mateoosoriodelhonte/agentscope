<script setup lang="ts">
withDefaults(defineProps<{ kind: string; detail?: string }>(), {
  detail: 'Added by the AgentScope harness to test resilience; the agent did not cause it.',
})
</script>

<template>
  <p class="banner" role="note">
    <span class="hatch" aria-hidden="true"></span>
    <span><strong>Injected fault: {{ kind }}.</strong> {{ detail }}</span>
  </p>
</template>

<style scoped>
.banner {
  display: flex;
  align-items: stretch;
  gap: 10px;
  margin: 0 0 10px;
  padding: 6px 10px 6px 0;
  overflow: hidden;
  border: 1px solid var(--injected);
  border-radius: var(--radius);
  background: var(--injected-soft);
  color: var(--text);
  font-size: 12.5px;
}
.hatch {
  flex: none;
  width: 14px;
  margin: -6px 0;
  background: var(--hatch);
}
strong {
  color: var(--injected);
}
</style>
