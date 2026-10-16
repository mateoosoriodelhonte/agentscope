<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, shallowRef, watch } from 'vue'
import { RouterLink } from 'vue-router'
import { api, ApiError } from '@/api/client'
import type { CheckName, RunDetail } from '@/api/types'
import BarModeToggle from '@/components/trace/BarModeToggle.vue'
import type { BarMode } from '@/components/trace/DurationBar.vue'
import KeyboardHelp from '@/components/trace/KeyboardHelp.vue'
import RunHeader from '@/components/trace/RunHeader.vue'
import SpanDetail from '@/components/trace/SpanDetail.vue'
import { detailTabs } from '@/components/trace/spanTabs'
import SpanTreeTable from '@/components/trace/SpanTreeTable.vue'
import TransitionStrip from '@/components/trace/TransitionStrip.vue'
import VerdictPanel from '@/components/trace/VerdictPanel.vue'
import { copyText } from '@/components/ui/CopyButton.vue'
import EmptyState from '@/components/ui/EmptyState.vue'
import Kbd from '@/components/ui/Kbd.vue'
import Skeleton from '@/components/ui/Skeleton.vue'
import SplitPane from '@/components/ui/SplitPane.vue'
import { shortId } from '@/composables/format'
import { matchBinding, TREE_ONLY_KEYS } from '@/composables/keybindings'
import { setPageDetail } from '@/composables/usePageMeta'
import { param, useQueryState } from '@/composables/useQueryState'
import { ancestorIds, useTraceModel } from '@/composables/useTraceModel'

const props = defineProps<{ runId: string }>()

const detail = shallowRef<RunDetail | null>(null)
const error = ref<{ status: number | null; message: string } | null>(null)
const page = ref<HTMLElement | null>(null)
let controller: AbortController | null = null

/**
 * The loading, error and loaded states each render their own h1. When the router has moved
 * focus to the loading heading, keep it on the page heading across the swap instead of
 * dropping it to <body>.
 */
async function keepHeadingFocus(swap: () => void): Promise<void> {
  const heading = page.value?.querySelector('h1')
  const focused = heading != null && heading === document.activeElement
  swap()
  if (!focused) return
  await nextTick()
  page.value?.querySelector<HTMLElement>('h1')?.focus()
}

async function load(): Promise<void> {
  controller?.abort()
  const current = new AbortController()
  controller = current
  detail.value = null
  error.value = null
  expanded.value = []
  try {
    const result = await api.getRun(props.runId, current.signal)
    await keepHeadingFocus(() => (detail.value = result))
    if (current.signal.aborted) return
    setPageDetail(`${result.trace.agent}@${result.trace.agent_version} · ${shortId(result.trace.run_id)}`)
    // A deep link to a check (e.g. printed by the CLI for a failed case) opens its evidence.
    const first = evidenceOf(query.value.check)[0]
    if (first && !query.value.span) void set({ span: first })
  } catch (e) {
    if (current.signal.aborted) return
    await keepHeadingFocus(() => {
      error.value = {
        status: e instanceof ApiError ? e.status : null,
        message: e instanceof Error ? e.message : String(e),
      }
    })
  }
}
const model = computed(() => (detail.value ? useTraceModel(detail.value.trace) : null))

const { state: query, set } = useQueryState({
  span: param.string(),
  tab: param.string(),
  check: param.string(),
  mode: param.enum(['relative', 'timeline'] as const),
})

const mode = computed<BarMode>(() => query.value.mode ?? (model.value?.simulated ? 'relative' : 'timeline'))
const selectedId = computed(() => {
  const m = model.value
  if (!m) return ''
  const id = query.value.span
  return id && m.rowById.has(id) ? id : (m.runRowId ?? m.rows[0]?.id ?? '')
})
const selectedRow = computed(() => model.value?.rowById.get(selectedId.value) ?? null)

const expanded = ref<string[]>([])
// A selection made from the URL, the verdict panel or n/N reveals its row.
watch(
  [model, selectedId],
  ([m, id]) => {
    if (!m || !id) return
    const missing = ancestorIds(m, id).filter((a) => !expanded.value.includes(a))
    if (missing.length) expanded.value = [...expanded.value, ...missing]
  },
  { immediate: true },
)

function expandAll(): void {
  expanded.value = model.value?.rows.filter((r) => r.childIds.length).map((r) => r.id) ?? []
}

function select(id: string): void {
  void set({ span: id === model.value?.runRowId ? null : id })
}

const evaluation = computed(() => detail.value?.evaluation ?? null)

function evidenceOf(name: string | null): string[] {
  const m = model.value
  const check = evaluation.value?.checks.find((c) => c.name === name)
  if (!m || !check) return []
  const ids = check.evidence.map((e) => e.span_id).filter((id): id is string => !!id && m.rowById.has(id))
  return [...new Set(ids)]
}
const evidenceSpans = computed(() => evidenceOf(query.value.check))
const outlined = computed(() => new Set(evidenceSpans.value))
const citedBy = computed(() => {
  const counts = new Map<string, number>()
  for (const check of evaluation.value?.checks ?? []) {
    for (const id of new Set(check.evidence.map((e) => e.span_id))) {
      if (id) counts.set(id, (counts.get(id) ?? 0) + 1)
    }
  }
  return counts
})

watch(() => props.runId, load, { immediate: true })
onBeforeUnmount(() => controller?.abort())

function activateCheck(name: CheckName | null): void {
  const first = evidenceOf(name)[0]
  void set(first ? { check: name, span: first, tab: null } : { check: name })
}

function cycleEvidence(delta: number): void {
  const ids = evidenceSpans.value
  if (!ids.length) return
  const index = ids.indexOf(selectedId.value)
  const next = index === -1 ? (delta > 0 ? 0 : ids.length - 1) : (index + delta + ids.length) % ids.length
  void set({ span: ids[next], tab: null })
}

const tree = ref<InstanceType<typeof SpanTreeTable> | null>(null)
const helpOpen = ref(false)
const announcement = ref('')

async function copySelectedId(): Promise<void> {
  const span = selectedRow.value?.span
  if (!span) return
  announcement.value = (await copyText(span.span_id)) ? `Copied span id ${span.span_id}` : 'Copy failed'
}

function onKeydown(event: KeyboardEvent): void {
  const binding = matchBinding(event)
  if (!binding || !model.value) return
  // Arrows, Home, End and Enter keep their usual meaning outside the tree.
  if (TREE_ONLY_KEYS.has(event.key)) return
  switch (binding.action) {
    case 'next':
      tree.value?.move(1)
      break
    case 'prev':
      tree.value?.move(-1)
      break
    case 'expandAll':
      expandAll()
      break
    case 'collapseAll':
      expanded.value = []
      break
    case 'nextEvidence':
      cycleEvidence(1)
      break
    case 'prevEvidence':
      cycleEvidence(-1)
      break
    case 'escape':
      if (!query.value.check) return
      void set({ check: null })
      break
    case 'tab': {
      const tab = selectedRow.value ? detailTabs(selectedRow.value)[Number(event.key) - 1] : undefined
      if (!tab) return
      void set({ tab: tab.id })
      break
    }
    case 'toggleMode':
      void set({ mode: mode.value === 'relative' ? 'timeline' : 'relative' })
      break
    case 'copyId':
      void copySelectedId()
      break
    case 'help':
      helpOpen.value = true
      break
    default:
      return
  }
  event.preventDefault()
}
</script>

<template>
  <div ref="page" class="trace-page">
    <template v-if="error">
      <h1 tabindex="-1" class="pad">Run {{ shortId(runId) }}</h1>
      <EmptyState v-if="error.status === 404" title="Run not found" variant="error">
        <p>No run with id <code>{{ runId }}</code> exists in this database. It may have been purged.</p>
        <template #actions>
          <RouterLink to="/runs" class="btn">Back to Runs</RouterLink>
        </template>
      </EmptyState>
      <EmptyState v-else title="Could not load this run" variant="error">
        <p>{{ error.message }}</p>
        <p>Is the API running? Start it with <code>agentscope serve</code>.</p>
        <template #actions>
          <button type="button" class="btn" @click="load">Retry</button>
        </template>
      </EmptyState>
    </template>

    <div v-else-if="!detail || !model" class="pad" aria-busy="true">
      <h1 tabindex="-1">Run {{ shortId(runId) }}</h1>
      <Skeleton :rows="10" />
    </div>

    <template v-else>
      <RunHeader :trace="detail.trace" class="pad header" />
      <div class="region" role="region" aria-label="Trace explorer" @keydown="onKeydown">
        <TransitionStrip
          class="strip"
          :groups="model.groups"
          :current="selectedRow?.groupId ?? null"
          @select="select"
        />
        <SplitPane class="split" label="Resize the trace and detail panes" storage-key="agentscope.trace.split">
          <template #first>
            <div class="toolbar">
              <BarModeToggle :mode="mode" @update:mode="set({ mode: $event })" />
              <button type="button" class="btn ghost" @click="expandAll">Expand all <Kbd>]</Kbd></button>
              <button type="button" class="btn ghost" @click="expanded = []">Collapse all <Kbd>[</Kbd></button>
              <button type="button" class="btn ghost shortcuts" @click="helpOpen = true">Shortcuts <Kbd>?</Kbd></button>
            </div>
            <SpanTreeTable
              ref="tree"
              v-model:expanded="expanded"
              :selected="selectedId"
              :model="model"
              :mode="mode"
              :outlined="outlined"
              :cited-by="citedBy"
              @update:selected="select"
            />
          </template>
          <template #second>
            <div class="rail">
              <VerdictPanel
                :evaluation="evaluation"
                :eval-case="detail.case"
                :trace="detail.trace"
                :active-check="query.check"
                :evidence-spans="evidenceSpans"
                :selected-span="selectedId"
                @activate="activateCheck"
                @jump="select"
              />
              <SpanDetail
                v-if="selectedRow"
                :tab="query.tab"
                :row="selectedRow"
                :model="model"
                :trace="detail.trace"
                :evaluation="evaluation"
                @update:tab="set({ tab: $event })"
                @select="select"
              />
            </div>
          </template>
        </SplitPane>
        <p class="visually-hidden" aria-live="polite">{{ announcement }}</p>
      </div>
      <KeyboardHelp :open="helpOpen" @close="helpOpen = false" />
    </template>
  </div>
</template>

<style scoped>
.trace-page {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
}
.pad {
  padding: 16px 20px 0;
}
.header {
  padding-bottom: 10px;
  border-bottom: 1px solid var(--border);
  background: var(--surface-1);
}
.region {
  display: flex;
  flex: 1;
  flex-direction: column;
  min-height: 0;
}
.strip {
  padding: 8px 20px;
  border-bottom: 1px solid var(--border);
}
.split {
  flex: 1;
  min-height: 0;
}
.toolbar {
  position: sticky;
  top: 0;
  z-index: 2;
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 6px 8px;
  border-bottom: 1px solid var(--border);
  background: var(--surface-1);
}
.shortcuts {
  margin-left: auto;
}
.rail {
  display: grid;
  grid-template-columns: minmax(0, 1fr);
  gap: 18px;
  align-content: start;
  padding: 12px 16px 24px;
}
@media (max-width: 1099px) {
  .trace-page {
    height: auto;
  }
  .rail {
    border-top: 1px solid var(--border);
  }
}
</style>
