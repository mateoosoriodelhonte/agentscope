<script setup lang="ts">
import { computed, onMounted, ref, shallowRef } from 'vue'
import { api } from '@/api/client'
import type { ProviderStatus } from '@/api/types'
import Icon from '@/components/ui/Icon.vue'

const providers = shallowRef<ProviderStatus[] | null>(null)
const error = ref<string | null>(null)

onMounted(async () => {
  try {
    providers.value = await api.providers()
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e)
  }
})

const enabled = computed(() => providers.value?.filter((p) => p.enabled) ?? [])

function state(p: ProviderStatus): string {
  if (p.deterministic) return 'simulated'
  if (p.reachable === null) return 'not probed'
  return p.reachable ? 'reachable' : 'unreachable'
}

function describe(p: ProviderStatus): string {
  return [p.name, state(p), p.target, p.detail].filter(Boolean).join(' · ')
}
</script>

<template>
  <ul v-if="enabled.length" class="providers" aria-label="Model providers">
    <li v-for="p in enabled" :key="p.name" class="provider" :class="{ down: p.reachable === false }" :title="describe(p)">
      <Icon :name="p.deterministic ? 'flask' : p.reachable === false ? 'alert' : 'dot'" :size="12" />
      <span class="name mono">{{ p.name }}</span>
      <span class="state">{{ state(p) }}</span>
    </li>
  </ul>
  <span v-else-if="error" class="unknown" :title="`Could not load providers: ${error}`">
    providers unknown
  </span>
</template>

<style scoped>
.providers {
  display: flex;
  gap: 6px;
  margin: 0;
  padding: 0;
  list-style: none;
}
.provider {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  height: 22px;
  padding: 0 8px 0 6px;
  border: 1px solid var(--border);
  border-radius: 999px;
  background: var(--surface-2);
  font-size: 12px;
  color: var(--text-muted);
}
.provider :deep(.icon) {
  vertical-align: 0;
}
.provider.down {
  color: var(--warn);
  border-color: var(--warn);
}
.name {
  color: var(--text);
}
.unknown {
  font-size: 12px;
  color: var(--text-faint);
}
</style>
