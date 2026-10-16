<script setup lang="ts">
import { computed } from 'vue'
import type { CaseChange, CaseComparison } from '@/api/types'
import { plural } from '@/composables/format'

/*
 * Cases grouped by how they changed from A to B, most important first: regressions, fixes,
 * still failing (with different or the same failed checks), and still passing (collapsed).
 * Cases whose content changed are different tests sharing an id, so they are listed apart
 * and never counted as flips. Selecting a case opens its trace diff.
 */
const props = defineProps<{ cases: CaseComparison[]; selected: string | null }>()
const emit = defineEmits<{ select: [caseId: string] }>()

const FLIPS: { change: CaseChange; title: string; collapsed?: boolean }[] = [
  { change: 'regression', title: 'Regressed' },
  { change: 'fix', title: 'Fixed' },
  { change: 'still_failing_changed', title: 'Still failing (changed)' },
  { change: 'still_failing_same', title: 'Still failing (same)' },
  { change: 'still_passing', title: 'Still passing', collapsed: true },
]
const APART: { change: CaseChange; title: string; note: string }[] = [
  { change: 'content_changed', title: 'Content changed', note: 'same id, different case: not compared' },
  { change: 'removed', title: 'Only in A', note: 'not in B' },
  { change: 'added', title: 'Only in B', note: 'not in A' },
]

const byChange = computed(() => {
  const groups = new Map<CaseChange, CaseComparison[]>()
  for (const c of props.cases) groups.set(c.change, [...(groups.get(c.change) ?? []), c])
  return groups
})
const casesOf = (change: CaseChange) => byChange.value.get(change) ?? []
const diffable = (c: CaseComparison) => c.a_run_id !== null && c.b_run_id !== null
const checks = (list: string[]) => (list.length ? list.join(', ') : 'none')
</script>

<template>
  <div class="flips">
    <template v-for="group in FLIPS" :key="group.change">
      <details v-if="casesOf(group.change).length" class="group" :class="group.change" :open="!group.collapsed || undefined">
        <summary>
          <span class="title">{{ group.title }}</span>
          <span class="count mono">{{ casesOf(group.change).length }}</span>
        </summary>
        <ul class="cases">
          <li v-for="c in casesOf(group.change)" :key="c.case_id">
            <button
              type="button"
              class="case"
              :aria-pressed="selected === c.case_id"
              :disabled="!diffable(c)"
              :title="diffable(c) ? 'Show the trace diff' : 'A run is missing on one side, so there is no trace diff'"
              @click="emit('select', c.case_id)"
            >
              <span class="mono id">{{ c.case_id }}</span>
              <span class="failed">
                <span class="side">A failed: <span class="mono">{{ checks(c.a_failed_checks) }}</span></span>
                <span class="side">B failed: <span class="mono">{{ checks(c.b_failed_checks) }}</span></span>
              </span>
            </button>
          </li>
        </ul>
      </details>
    </template>
    <p v-if="!FLIPS.some((g) => casesOf(g.change).length)" class="faint none">No case was compared.</p>

    <section v-if="APART.some((g) => casesOf(g.change).length)" class="apart" aria-label="Cases not compared">
      <h3 class="section-title">Not compared</h3>
      <ul class="cases">
        <template v-for="group in APART" :key="group.change">
          <li v-for="c in casesOf(group.change)" :key="c.case_id">
            <button type="button" class="case" :aria-pressed="selected === c.case_id" :disabled="!diffable(c)" @click="emit('select', c.case_id)">
              <span class="mono id">{{ c.case_id }}</span>
              <span class="failed">{{ group.title }}: {{ group.note }}</span>
            </button>
          </li>
        </template>
      </ul>
      <p class="faint none">{{ plural(APART.reduce((n, g) => n + casesOf(g.change).length, 0), 'case') }} left out of every flip count.</p>
    </section>
  </div>
</template>

<style scoped>
.flips {
  display: grid;
  gap: 8px;
}
.group {
  border: 1px solid var(--border);
  border-radius: var(--radius);
  background: var(--surface-1);
}
.group.regression {
  border-left: 3px solid var(--fail);
}
.group.fix {
  border-left: 3px solid var(--ok);
}
summary {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 7px 10px;
  cursor: pointer;
  font-size: 13px;
  font-weight: 500;
}
.count {
  padding: 0 6px;
  border-radius: 999px;
  background: var(--surface-2);
  color: var(--text-muted);
}
.cases {
  display: grid;
  margin: 0;
  padding: 0;
  list-style: none;
}
.group .cases {
  border-top: 1px solid var(--border);
}
.case {
  display: grid;
  grid-template-columns: minmax(160px, 1fr) minmax(0, 2fr);
  gap: 10px;
  width: 100%;
  padding: 6px 10px;
  border: 0;
  border-bottom: 1px solid var(--border);
  background: none;
  text-align: left;
  cursor: pointer;
  font-size: 12.5px;
}
li:last-child .case {
  border-bottom: 0;
}
.case:hover:not(:disabled) {
  background: var(--hover);
}
.case:disabled {
  cursor: default;
  color: var(--text-muted);
}
.case[aria-pressed='true'] {
  background: var(--selected);
  box-shadow: inset 3px 0 0 var(--accent);
}
.id {
  color: var(--text);
  overflow-wrap: anywhere;
}
.failed {
  display: grid;
  gap: 1px;
  color: var(--text-muted);
}
.side .mono {
  font-size: 11.5px;
}
.apart {
  display: grid;
  gap: 6px;
  padding: 8px 10px;
  border: 1px dashed var(--border-strong);
  border-radius: var(--radius);
}
.none {
  margin: 0;
  font-size: 12.5px;
}
</style>
