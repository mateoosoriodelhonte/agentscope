<script setup lang="ts">
import type { ProviderStatus } from '@/api/types'
import StatusBadge from '@/components/ui/StatusBadge.vue'
import type { Outcome } from '@/composables/useTraceModel'

// Which model providers can run, and where each one sends requests (host:port only).
defineProps<{ providers: ProviderStatus[] }>()

function outcome(p: ProviderStatus): Outcome {
  if (!p.enabled) return { tone: 'na', label: 'disabled' }
  if (p.deterministic) return { tone: 'neutral', label: 'deterministic' }
  if (p.reachable === null) return { tone: 'na', label: 'not probed' }
  return p.reachable ? { tone: 'ok', label: 'reachable' } : { tone: 'warn', label: 'unreachable' }
}
</script>

<template>
  <section class="providers" aria-labelledby="providers-title">
    <h2 id="providers-title" class="section-title">Model providers</h2>
    <p v-if="!providers.length" class="faint none">The API reported no providers.</p>
    <ul v-else class="list">
      <li v-for="p in providers" :key="p.name" class="provider panel">
        <div class="line">
          <span class="name mono">{{ p.name }}</span>
          <StatusBadge :outcome="outcome(p)" />
        </div>
        <p v-if="p.deterministic" class="text">
          Scripted, deterministic test double: demonstrates harness mechanics, not model behaviour. Never contacts
          the network.
        </p>
        <p v-else-if="p.enabled && p.reachable === false" class="text">
          Unreachable at <code>{{ p.target ?? 'the configured address' }}</code>. Start the server there, or point
          AgentScope at it with <code>AGENTSCOPE_OLLAMA_URL</code>.
        </p>
        <p v-else-if="p.target" class="text">
          Target <code>{{ p.target }}</code>
        </p>
        <p v-if="p.models.length" class="text models">
          Models: <span v-for="m in p.models" :key="m" class="tag">{{ m }}</span>
        </p>
        <p v-if="p.detail" class="text faint">{{ p.detail }}</p>
      </li>
    </ul>
  </section>
</template>

<style scoped>
.providers {
  display: grid;
  gap: 8px;
  align-content: start;
}
.none {
  margin: 0;
  font-size: 13px;
}
.list {
  display: grid;
  gap: 6px;
  margin: 0;
  padding: 0;
  list-style: none;
}
.provider {
  padding: 8px 10px;
}
.line {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}
.name {
  font-size: 12.5px;
  color: var(--text);
}
.text {
  margin: 4px 0 0;
  font-size: 12.5px;
  color: var(--text-muted);
}
.text code {
  color: var(--text);
}
.models {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 4px;
}
</style>
