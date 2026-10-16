<script setup lang="ts">
import Icon from '@/components/ui/Icon.vue'
import { TONE_ICONS } from '@/components/ui/status'
import type { StepGroup } from '@/composables/useTraceModel'

/*
 * The run compressed to one segment per step. Normal loop transitions are implied; only
 * off-loop transitions (denials, retries, repairs, abnormal ends) get reason chips. It also
 * works as a minimap: activating a segment selects that step in the tree.
 */
defineProps<{ groups: StepGroup[]; current: string | null }>()
const emit = defineEmits<{ select: [groupId: string] }>()
</script>

<template>
  <nav class="strip-wrap" aria-label="Steps">
    <ol class="strip">
      <li v-for="group in groups" :key="group.id" class="item">
        <button
          type="button"
          class="segment"
          :class="[group.outcome.tone, { current: group.id === current, fault: group.hasFault }]"
          :aria-current="group.id === current ? 'step' : undefined"
          :aria-label="`${group.label}: ${group.narrative}`"
          :title="group.narrative"
          @click="emit('select', group.id)"
        >
          <Icon v-if="group.outcome.tone !== 'neutral'" :name="TONE_ICONS[group.outcome.tone]" :size="11" />
          <span>{{ group.kind === 'finish' ? 'End' : `S${group.index}` }}</span>
        </button>
        <span v-for="(chip, i) in group.chips" :key="i" class="chip" :class="[chip.kind, chip.tone]" :title="chip.title">
          {{ chip.label }}
        </span>
      </li>
    </ol>
  </nav>
</template>

<style scoped>
.strip-wrap {
  overflow-x: auto;
}
.strip {
  display: flex;
  align-items: center;
  gap: 4px;
  margin: 0;
  padding: 0;
  list-style: none;
}
.item {
  display: flex;
  align-items: center;
  gap: 4px;
}
.item + .item::before {
  content: '';
  width: 8px;
  height: 1px;
  background: var(--border-strong);
}
.segment {
  display: inline-flex;
  align-items: center;
  gap: 3px;
  height: 22px;
  min-width: 34px;
  padding: 0 7px;
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  background: var(--surface-1);
  color: var(--text);
  font-family: var(--font-mono);
  font-size: 11.5px;
  cursor: pointer;
}
.segment :deep(.icon) {
  vertical-align: 0;
}
.segment:hover {
  background: var(--hover);
}
.segment.ok :deep(.icon) {
  color: var(--ok);
}
.segment.fail {
  border-color: var(--fail);
  color: var(--fail);
}
.segment.warn {
  border-color: var(--warn);
  color: var(--warn);
}
.segment.fault {
  border-bottom: 3px solid var(--injected);
}
.segment.current {
  background: var(--selected);
  box-shadow: 0 0 0 1px var(--accent);
}
.chip {
  display: inline-flex;
  align-items: center;
  height: 20px;
  padding: 0 6px;
  border-radius: 999px;
  font-size: 11px;
  white-space: nowrap;
}
.chip.warn {
  background: var(--warn-soft);
  color: var(--warn);
}
.chip.fail {
  background: var(--fail-soft);
  color: var(--fail);
}
</style>
