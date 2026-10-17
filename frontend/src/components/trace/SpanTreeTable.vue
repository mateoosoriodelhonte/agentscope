<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'
import Icon from '@/components/ui/Icon.vue'
import StatusBadge from '@/components/ui/StatusBadge.vue'
import { matchBinding, TREE_ACTIONS } from '@/composables/keybindings'
import { formatDuration, plural } from '@/composables/format'
import { visibleRows, type TraceModel, type TraceRow } from '@/composables/useTraceModel'
import DurationBar, { type BarMode } from './DurationBar.vue'

/*
 * One treegrid for the whole trace: steps at the top level, then node spans, then chat,
 * tool and guardrail spans. Roving tabindex; selection follows focus.
 */
const props = withDefaults(
  defineProps<{
    model: TraceModel
    mode: BarMode
    outlined?: ReadonlySet<string>
    citedBy?: ReadonlyMap<string, number>
  }>(),
  { outlined: () => new Set(), citedBy: () => new Map() },
)
const selected = defineModel<string>('selected', { required: true })
const expanded = defineModel<string[]>('expanded', { required: true })

const table = ref<HTMLTableElement | null>(null)
const expandedSet = computed(() => new Set(expanded.value))
const visible = computed(() => visibleRows(props.model, expandedSet.value))

/** The selected row, or its nearest visible ancestor when it is collapsed away. */
const focusId = computed(() => {
  const shown = new Set(visible.value.map((r) => r.id))
  let id: string | null = selected.value
  while (id && !shown.has(id)) id = props.model.rowById.get(id)?.parentId ?? null
  return id ?? visible.value[0]?.id ?? null
})

const groupsWithEvidence = computed(() => {
  const out = new Set<string>()
  for (const id of props.outlined) {
    const row = props.model.rowById.get(id)
    if (row) out.add(row.groupId)
  }
  return out
})

function siblings(row: TraceRow): string[] {
  const parent = row.parentId ? props.model.rowById.get(row.parentId) : null
  return parent ? parent.childIds : props.model.rows.filter((r) => r.level === 1).map((r) => r.id)
}

const domId = (id: string) => `trace-row-${id}`

function setExpanded(id: string, open: boolean): void {
  const has = expandedSet.value.has(id)
  if (open && !has) expanded.value = [...expanded.value, id]
  if (!open && has) expanded.value = expanded.value.filter((e) => e !== id)
}

function toggle(row: TraceRow): void {
  if (row.childIds.length) setExpanded(row.id, !expandedSet.value.has(row.id))
}

/** Moves the selection by `delta` visible rows. Exposed for the j/k shortcuts. */
function move(delta: number): void {
  const rows = visible.value
  const index = rows.findIndex((r) => r.id === focusId.value)
  const next = rows[Math.min(rows.length - 1, Math.max(0, index + delta))]
  if (next) selected.value = next.id
}

function onKeydown(event: KeyboardEvent): void {
  const binding = matchBinding(event)
  if (!binding || !TREE_ACTIONS.has(binding.action)) return
  const row = focusId.value ? props.model.rowById.get(focusId.value) : undefined
  if (!row) return
  const open = expandedSet.value.has(row.id)
  switch (binding.action) {
    case 'next':
      move(1)
      break
    case 'prev':
      move(-1)
      break
    case 'first':
      selected.value = visible.value[0]?.id ?? selected.value
      break
    case 'last':
      selected.value = visible.value[visible.value.length - 1]?.id ?? selected.value
      break
    case 'expand':
      if (row.childIds.length && !open) setExpanded(row.id, true)
      else if (open && row.childIds[0]) selected.value = row.childIds[0]
      break
    case 'collapse':
      if (open) setExpanded(row.id, false)
      else if (row.parentId) selected.value = row.parentId
      break
    case 'toggle':
      toggle(row)
      break
  }
  event.preventDefault()
  event.stopPropagation()
}

// Keep the focus row in view, and move focus with it while the tree has focus. The focus row
// changes with the selection and also when collapsing hides the focused row (e.g. "[" on a
// span inside a step): the row is removed with focus in it, so whether the tree had focus is
// read before the DOM update (a pre-flush watcher), and focus then lands on the ancestor
// instead of falling to <body>, where no trace shortcut works.
watch([selected, focusId], async () => {
  const hadFocus = table.value?.contains(document.activeElement) ?? false
  await nextTick()
  const id = focusId.value
  const el = id ? document.getElementById(domId(id)) : null
  if (!el) return
  el.scrollIntoView?.({ block: 'nearest' })
  if (hadFocus || table.value?.contains(document.activeElement)) el.focus({ preventScroll: true })
})

defineExpose({ move })
</script>

<template>
  <table
    ref="table"
    class="tree"
    role="treegrid"
    aria-label="Steps and spans"
    @keydown="onKeydown"
  >
    <colgroup>
      <col class="c-name" />
      <col class="c-outcome" />
      <col class="c-duration" />
      <col class="c-bar" />
    </colgroup>
    <thead>
      <tr role="row">
        <th role="columnheader" scope="col">Name</th>
        <th role="columnheader" scope="col">Outcome</th>
        <th role="columnheader" scope="col" class="num">Duration</th>
        <th role="columnheader" scope="col">
          {{ mode === 'relative' ? 'Relative' : 'Timeline' }}
          <span
            v-if="mode === 'relative' && model.toolTimeoutMs <= model.relativeScaleMs"
            class="legend"
            :title="`Dashed tick: the tool timeout (${formatDuration(model.toolTimeoutMs)})`"
          >
            ┆ timeout
          </span>
        </th>
      </tr>
    </thead>
    <tbody>
      <tr
        v-for="row in visible"
        :id="domId(row.id)"
        :key="row.id"
        role="row"
        :aria-level="row.level"
        :aria-setsize="siblings(row).length"
        :aria-posinset="siblings(row).indexOf(row.id) + 1"
        :aria-expanded="row.childIds.length ? expandedSet.has(row.id) : undefined"
        :aria-selected="row.id === selected"
        :tabindex="row.id === focusId ? 0 : -1"
        :class="[
          `level-${row.level}`,
          `kind-${row.kind}`,
          {
            selected: row.id === selected,
            evidence: outlined.has(row.id),
            'has-evidence': row.level === 1 && !expandedSet.has(row.id) && groupsWithEvidence.has(row.id),
          },
        ]"
        @click="selected = row.id"
      >
        <td role="gridcell">
          <div class="name">
            <span class="gutter" aria-hidden="true"></span>
            <span
              v-if="row.childIds.length"
              class="twisty"
              aria-hidden="true"
              @click.stop="toggle(row)"
            >
              <Icon :name="expandedSet.has(row.id) ? 'chevronDown' : 'chevronRight'" :size="12" />
            </span>
            <span v-else class="twisty"></span>
            <span class="label" :class="{ mono: row.level > 1 }">{{ row.label }}</span>
            <span v-if="row.narrative" class="narrative" :title="row.narrative">{{ row.narrative }}</span>
            <span v-if="row.fault" class="fault">injected {{ row.fault }}</span>
            <span
              v-if="citedBy.get(row.id)"
              class="cited"
              :title="`Cited as evidence by ${plural(citedBy.get(row.id) ?? 0, 'check')}`"
            >
              cited ×{{ citedBy.get(row.id) }}
            </span>
          </div>
        </td>
        <td role="gridcell" class="outcome"><StatusBadge :outcome="row.outcome" variant="plain" /></td>
        <td role="gridcell" class="num mono duration">{{ formatDuration(row.durationMs) }}</td>
        <td role="gridcell" class="bar">
          <DurationBar
            v-if="row.kind !== 'run' || mode === 'timeline'"
            :mode="mode"
            :start-ms="row.startMs"
            :duration-ms="row.durationMs"
            :total-ms="model.totalMs"
            :scale-ms="model.relativeScaleMs"
            :tick-ms="model.toolTimeoutMs"
            :timeline-tick="row.kind === 'tool'"
            :fault="row.fault !== null"
            :tone="row.outcome.tone === 'fail' ? 'fail' : 'neutral'"
          />
        </td>
      </tr>
    </tbody>
  </table>
</template>

<style scoped>
.tree {
  width: 100%;
  border-collapse: collapse;
  table-layout: fixed;
  font-size: 13px;
}
.c-outcome {
  width: 108px;
}
.c-duration {
  width: 76px;
}
.c-bar {
  width: 120px;
}
th {
  position: sticky;
  top: 0;
  z-index: 1;
  padding: 6px 8px;
  border-bottom: 1px solid var(--border);
  background: var(--surface-2);
  color: var(--text-muted);
  font-size: 11.5px;
  font-weight: 500;
  text-align: left;
  white-space: nowrap;
}
th.num {
  text-align: right;
}
.legend {
  margin-left: 6px;
  color: var(--fail);
  font-weight: 400;
}
tr[role='row'] {
  cursor: default;
}
tbody tr {
  height: 30px;
  border-bottom: 1px solid var(--border);
}
tbody tr:hover {
  background: var(--hover);
}
tbody tr.selected {
  background: var(--selected);
}
tbody tr:focus-visible {
  outline: 2px solid var(--focus);
  outline-offset: -2px;
}
tbody tr.evidence td:first-child,
tbody tr.has-evidence td:first-child {
  box-shadow: inset 2px 0 0 var(--accent);
}
tbody tr.evidence {
  outline: 1px dashed var(--accent);
  outline-offset: -1px;
}
td {
  padding: 0 8px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.name {
  position: relative;
  display: flex;
  align-items: center;
  gap: 6px;
  height: 30px;
  min-width: 0;
}
.level-2 .name {
  padding-left: 26px;
}
.level-3 .name {
  padding-left: 46px;
}
.gutter {
  position: absolute;
  top: 5px;
  bottom: 5px;
  width: 3px;
  border-radius: 2px;
}
.level-1 .gutter {
  display: none;
}
.level-2 .gutter {
  left: 16px;
}
.level-3 .gutter {
  left: 36px;
}
.kind-node .gutter {
  background: var(--kind-node);
}
.kind-llm .gutter {
  background: var(--kind-llm);
}
.kind-tool .gutter {
  background: var(--kind-tool);
}
.kind-guardrail .gutter {
  background: var(--kind-guardrail);
}
.twisty {
  display: inline-grid;
  place-items: center;
  flex: none;
  width: 16px;
  height: 16px;
  color: var(--text-muted);
  cursor: pointer;
}
.label {
  flex: none;
  font-weight: 500;
}
.label.mono {
  flex: 0 1 auto;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  font-weight: 400;
}
.level-1 .label {
  min-width: 48px;
}
.narrative {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  color: var(--text-muted);
}
.fault,
.cited {
  flex: none;
  padding: 0 5px;
  border-radius: var(--radius-sm);
  font-size: 11px;
  line-height: 16px;
}
.fault {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  background: var(--injected-soft);
  color: var(--injected);
}
.fault::before {
  content: '';
  width: 8px;
  height: 10px;
  background: var(--hatch);
}
.cited {
  background: var(--accent-soft);
  color: var(--accent);
}
.duration {
  color: var(--text-muted);
  text-align: right;
}
.bar {
  padding-right: 12px;
}
</style>
