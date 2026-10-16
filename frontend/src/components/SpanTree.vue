<script setup lang="ts">
import { computed } from 'vue'
import type { SpanRecord } from '@/api/types'

const props = defineProps<{ spans: SpanRecord[]; selected: string | null }>()
const emit = defineEmits<{ select: [spanId: string] }>()

interface Row {
  span: SpanRecord
  depth: number
}

const rows = computed<Row[]>(() => {
  const children = new Map<string | null, SpanRecord[]>()
  const ids = new Set(props.spans.map((s) => s.span_id))
  for (const span of props.spans) {
    const parent = span.parent_span_id && ids.has(span.parent_span_id) ? span.parent_span_id : null
    const list = children.get(parent) ?? []
    list.push(span)
    children.set(parent, list)
  }
  const out: Row[] = []
  const walk = (parent: string | null, depth: number) => {
    for (const span of (children.get(parent) ?? []).sort((a, b) => a.start_time.localeCompare(b.start_time))) {
      out.push({ span, depth })
      walk(span.span_id, depth + 1)
    }
  }
  walk(null, 0)
  return out
})

const total = computed(() => Math.max(...props.spans.map((s) => s.duration_ms), 0.001))
</script>

<template>
  <ul class="tree" role="tree" aria-label="Spans">
    <li
      v-for="row in rows"
      :key="row.span.span_id"
      role="treeitem"
      :aria-selected="row.span.span_id === selected"
      :class="['row', row.span.kind, { selected: row.span.span_id === selected, error: row.span.status === 'error' }]"
      tabindex="0"
      @click="emit('select', row.span.span_id)"
      @keydown.enter="emit('select', row.span.span_id)"
    >
      <span class="name" :style="{ paddingLeft: `${row.depth * 16}px` }">
        <span class="kind-dot" aria-hidden="true"></span>{{ row.span.name }}
      </span>
      <span class="bar" aria-hidden="true">
        <span :style="{ width: `${Math.max(2, (row.span.duration_ms / total) * 100)}%` }"></span>
      </span>
      <span class="duration mono">{{ row.span.duration_ms.toFixed(1) }} ms</span>
    </li>
  </ul>
</template>

<style scoped>
.tree {
  list-style: none;
  margin: 0;
  padding: 0;
}
.row {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 120px 80px;
  gap: 12px;
  align-items: center;
  padding: 4px 8px;
  border-radius: 4px;
  cursor: pointer;
}
.row:hover {
  background: var(--surface-2);
}
.row.selected {
  background: var(--accent-soft);
}
.name {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.kind-dot {
  display: inline-block;
  width: 8px;
  height: 8px;
  border-radius: 2px;
  margin-right: 8px;
  background: var(--kind-node);
}
.llm .kind-dot {
  background: var(--kind-llm);
}
.tool .kind-dot {
  background: var(--kind-tool);
}
.guardrail .kind-dot {
  background: var(--kind-guardrail);
}
.error .name {
  color: var(--fail);
}
.bar {
  height: 6px;
  background: var(--surface-2);
  border-radius: 3px;
  overflow: hidden;
}
.bar span {
  display: block;
  height: 100%;
  background: var(--border-strong);
}
.duration {
  text-align: right;
  color: var(--text-muted);
}
</style>
