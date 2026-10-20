<script setup lang="ts">
import { RouterLink } from 'vue-router'
import Icon from '@/components/ui/Icon.vue'

/*
 * One dimension (or pair) a flipped case depends on. Solid: applied alone to A it already
 * gives B's verdict. Outlined: removed from B it gives back A's verdict. The column it sits
 * in says the same in words. The label opens that variant's run of the case, the icon its
 * trace diff against A's run; without a recorded run the chip is plain text.
 */
defineProps<{
  label: string
  /** The variant in words, e.g. "A + prompt". */
  variant: string
  kind: 'solid' | 'outlined'
  caseId: string
  runId: string | null
  aRunId: string | null
}>()
</script>

<template>
  <span class="chip" :class="kind">
    <RouterLink
      v-if="runId"
      :to="`/runs/${encodeURIComponent(runId)}`"
      class="run"
      :aria-label="`${label}: the ${variant} run of ${caseId}`"
    >
      {{ label }}
    </RouterLink>
    <span v-else class="run" :title="`No recorded ${variant} run of this case`">{{ label }}</span>
    <RouterLink
      v-if="runId && aRunId"
      :to="{ path: '/compare/runs', query: { a: aRunId, b: runId } }"
      class="diff"
      :aria-label="`Trace diff of ${variant} against A on ${caseId}`"
      :title="`Trace diff of ${variant} against A`"
    >
      <Icon name="compare" :size="12" />
    </RouterLink>
  </span>
</template>

<style scoped>
.chip {
  display: inline-flex;
  align-items: stretch;
  border: 1px solid var(--accent);
  border-radius: 999px;
  font-size: 12px;
  line-height: 20px;
  white-space: nowrap;
}
.chip.solid {
  background: var(--accent);
  color: var(--accent-contrast);
}
.chip.outlined {
  background: var(--surface-1);
  color: var(--accent);
}
.run,
.diff {
  display: inline-flex;
  align-items: center;
  color: inherit;
}
.run {
  padding: 0 8px;
}
.diff {
  padding: 0 7px 0 5px;
  border-left: 1px solid currentColor;
}
a.run:hover,
.diff:hover {
  text-decoration: underline;
}
.chip :deep(.icon) {
  vertical-align: 0;
}
</style>
