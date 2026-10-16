<script setup lang="ts">
import { computed, onMounted, ref, shallowRef } from 'vue'
import { api } from '@/api/client'
import type { RunDetail } from '@/api/types'
import JsonView from '@/components/JsonView.vue'
import SpanTree from '@/components/SpanTree.vue'
import StatusBadge from '@/components/StatusBadge.vue'
import VerdictPanel from '@/components/VerdictPanel.vue'

const props = defineProps<{ runId: string }>()
const detail = shallowRef<RunDetail | null>(null)
const error = ref<string | null>(null)
const selected = ref<string | null>(null)

onMounted(async () => {
  try {
    detail.value = await api.getRun(props.runId)
    selected.value = detail.value.trace.spans[0]?.span_id ?? null
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e)
  }
})

const span = computed(() => detail.value?.trace.spans.find((s) => s.span_id === selected.value) ?? null)
const usage = computed(() => {
  const u = detail.value?.trace.usage
  return u ? `${u.input_tokens ?? '?'} in / ${u.output_tokens ?? '?'} out` : 'not reported'
})
</script>

<template>
  <p v-if="error" class="error" role="alert">Could not load run: {{ error }}</p>
  <article v-else-if="detail" class="trace">
    <header class="head">
      <div>
        <h1>{{ detail.trace.task }}</h1>
        <p class="meta mono">
          {{ detail.trace.agent }}@{{ detail.trace.agent_version }} · {{ detail.trace.provider }}/{{ detail.trace.model }} ·
          {{ detail.trace.steps }} steps · {{ detail.trace.duration_ms.toFixed(0) }} ms · tokens {{ usage }}
        </p>
      </div>
      <StatusBadge :status="detail.trace.status" />
    </header>
    <ol class="transitions" aria-label="State transitions">
      <li v-for="t in detail.trace.transitions" :key="t.seq" :title="t.reason">
        <span class="mono">{{ t.source }} → {{ t.target }}</span>
      </li>
    </ol>
    <div class="panes">
      <section class="pane">
        <SpanTree :spans="detail.trace.spans" :selected="selected" @select="selected = $event" />
      </section>
      <aside class="pane side">
        <VerdictPanel :evaluation="detail.evaluation" @select="selected = $event" />
        <section v-if="span" class="span-detail">
          <h2>{{ span.name }}</h2>
          <p v-if="span.status_message" class="error">{{ span.status_message }}</p>
          <h3>Input</h3>
          <JsonView :value="span.input" />
          <h3>Output</h3>
          <JsonView :value="span.output" />
          <h3>Attributes</h3>
          <JsonView :value="span.attributes" />
        </section>
      </aside>
    </div>
  </article>
</template>

<style scoped>
.head {
  display: flex;
  justify-content: space-between;
  gap: 16px;
  align-items: flex-start;
}
h1 {
  font-size: 18px;
}
.meta {
  margin: 4px 0 0;
  color: var(--text-muted);
}
.transitions {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  list-style: none;
  padding: 0;
  margin: 16px 0;
}
.transitions li {
  padding: 2px 8px;
  border: 1px solid var(--border);
  border-radius: 4px;
  background: var(--surface-1);
}
.panes {
  display: grid;
  grid-template-columns: minmax(0, 3fr) minmax(0, 2fr);
  gap: 16px;
}
.pane {
  background: var(--surface-1);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 8px;
}
.side {
  display: grid;
  gap: 16px;
  align-content: start;
  padding: 12px;
}
.span-detail h2 {
  font-size: 14px;
}
.span-detail h3 {
  font-size: 12px;
  color: var(--text-muted);
  margin: 12px 0 4px;
}
.error {
  color: var(--fail);
}
</style>
