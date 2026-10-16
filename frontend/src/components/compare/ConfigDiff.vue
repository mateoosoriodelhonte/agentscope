<script setup lang="ts">
import { computed } from 'vue'
import type { ConfigDifference, ConfigDimension } from '@/api/types'
import Icon from '@/components/ui/Icon.vue'
import { compact } from '@/composables/format'

/*
 * What changed between the two configurations, grouped by behaviour dimension. When more
 * than one dimension changed, no difference in results can be credited to any single one,
 * and the page says so prominently.
 */
const props = defineProps<{ diff: ConfigDifference[]; confounded: boolean }>()

const ORDER: ConfigDimension[] = ['prompt', 'model', 'tool_schema', 'graph', 'retry', 'isolation', 'tools', 'limits', 'other']
const LABELS: Record<ConfigDimension, string> = {
  prompt: 'Prompt',
  model: 'Model',
  tool_schema: 'Tool schemas',
  graph: 'Graph',
  retry: 'Retry policy',
  isolation: 'Untrusted-output isolation',
  tools: 'Permitted tools',
  limits: 'Limits',
  other: 'Other',
}

const groups = computed(() =>
  ORDER.map((dimension) => ({ dimension, items: props.diff.filter((d) => d.dimension === dimension) })).filter(
    (group) => group.items.length,
  ),
)
const isConfounded = computed(() => props.confounded || groups.value.length > 1)
</script>

<template>
  <section class="config-diff" aria-labelledby="config-diff-title">
    <h2 id="config-diff-title" class="section-title">Configuration differences</h2>
    <p v-if="isConfounded" class="callout fail confounded" role="note">
      <Icon name="alert" :size="16" />
      <span>
        <strong>Confounded: attribution not possible.</strong>
        {{ groups.length }} dimensions changed at once ({{ groups.map((g) => LABELS[g.dimension].toLowerCase()).join(', ') }}),
        so no difference in results can be credited to any one of them.
      </span>
    </p>
    <p v-else-if="groups.length === 1" class="callout info" role="note">
      <Icon name="check" :size="14" />
      <span>Only one dimension changed ({{ LABELS[groups[0]!.dimension].toLowerCase() }}).</span>
    </p>
    <p v-else class="callout info" role="note">
      <Icon name="check" :size="14" />
      <span>The two configurations are identical.</span>
    </p>
    <div v-if="groups.length" class="table-wrap panel">
      <table class="data-table">
        <caption class="visually-hidden">Configuration differences by dimension</caption>
        <thead>
          <tr>
            <th scope="col">Path</th>
            <th scope="col">A</th>
            <th scope="col">B</th>
          </tr>
        </thead>
        <tbody v-for="group in groups" :key="group.dimension">
          <tr class="group">
            <th scope="rowgroup" colspan="3">{{ LABELS[group.dimension] }}</th>
          </tr>
          <tr v-for="item in group.items" :key="item.path">
            <th scope="row" class="mono path">{{ item.path }}</th>
            <td class="mono value">{{ compact(item.a, 80) }}</td>
            <td class="mono value">{{ compact(item.b, 80) }}</td>
          </tr>
        </tbody>
      </table>
    </div>
  </section>
</template>

<style scoped>
.config-diff {
  display: grid;
  gap: 8px;
}
.confounded {
  font-size: 14px;
}
.confounded strong {
  margin-right: 4px;
}
.group th {
  padding-top: 10px;
  background: var(--surface-0);
  font-size: 11px;
  font-weight: 600;
  letter-spacing: 0.04em;
  text-transform: uppercase;
  color: var(--text-muted);
}
.path {
  font-weight: 400;
  white-space: nowrap;
}
.value {
  overflow-wrap: anywhere;
}
</style>
