<script setup lang="ts">
import { computed, nextTick, watch } from 'vue'
import { RouterLink } from 'vue-router'
import { api } from '@/api/client'
import type { EvalCase } from '@/api/types'
import { expectationSummary, faultLabels } from '@/components/datasets/caseSummary'
import CopyButton from '@/components/ui/CopyButton.vue'
import EmptyState from '@/components/ui/EmptyState.vue'
import ErrorState from '@/components/ui/ErrorState.vue'
import JsonView from '@/components/ui/JsonView.vue'
import { asJson } from '@/components/ui/jsonLines'
import Modal from '@/components/ui/Modal.vue'
import Skeleton from '@/components/ui/Skeleton.vue'
import { formatTimestamp, plural } from '@/composables/format'
import { openNewRun } from '@/composables/newRun'
import { setPageDetail } from '@/composables/usePageMeta'
import { param, useQueryState } from '@/composables/useQueryState'
import { useResource } from '@/composables/useResource'

const props = defineProps<{ name: string; version: string }>()
const datasetRef = computed(() => `${props.name}@${props.version}`)
const detail = useResource((signal) => api.getDataset(props.name, props.version, signal), () => datasetRef.value)
const { state: query, set } = useQueryState({ case: param.string(), tag: param.string() })

const dataset = computed(() => detail.data.value?.dataset ?? null)
const summary = computed(() => detail.data.value?.summary ?? null)
const tags = computed(() => [...new Set(dataset.value?.cases.flatMap((c) => c.tags) ?? [])].sort())
const cases = computed(() => (dataset.value?.cases ?? []).filter((c) => !query.value.tag || c.tags.includes(query.value.tag)))
const openCase = computed(() => dataset.value?.cases.find((c) => c.id === query.value.case) ?? null)
const exportUrl = computed(() => {
  try {
    return api.datasetExportUrl(props.name, props.version)
  } catch {
    return null
  }
})
const evaluateLink = computed(() => ({ path: '/evaluations', query: { new: datasetRef.value } }))

watch(dataset, (value) => setPageDetail(value ? datasetRef.value : null), { immediate: true })

/** Closes the case drawer first, so focus returns to the page before the run drawer opens. */
async function runCase(c: EvalCase): Promise<void> {
  if (query.value.case) {
    await set({ case: null })
    await nextTick()
  }
  openNewRun({
    agent: c.agent ?? dataset.value?.agent ?? undefined,
    task: c.task,
    faults: c.faults,
    modelFaults: c.model_faults,
    expected: c.expected,
    source: `case ${c.id} of ${datasetRef.value}`,
  })
}

function onTag(event: Event): void {
  void set({ tag: (event.target as HTMLSelectElement).value || null })
}
</script>

<template>
  <div class="screen">
    <header class="screen-head">
      <div>
        <h1 tabindex="-1" class="mono-title">{{ datasetRef }}</h1>
        <p v-if="dataset?.description" class="subtitle">{{ dataset.description }}</p>
      </div>
      <div v-if="dataset" class="actions">
        <a v-if="exportUrl" :href="exportUrl" class="btn" download>Export JSON</a>
        <RouterLink :to="evaluateLink" class="btn primary">Evaluate this dataset</RouterLink>
      </div>
    </header>

    <ErrorState v-if="detail.error.value && !detail.data.value" :error="detail.error.value" title="Could not load this dataset" @retry="detail.reload">
      <template #actions>
        <RouterLink to="/datasets" class="btn">All datasets</RouterLink>
      </template>
    </ErrorState>
    <Skeleton v-else-if="!dataset || !summary" :rows="8" />

    <template v-else>
      <dl class="facts">
        <div><dt>Cases</dt><dd class="mono">{{ summary.case_count }}</dd></div>
        <div><dt>Agent</dt><dd class="mono">{{ dataset.agent ?? 'any' }}</dd></div>
        <div>
          <dt>Content hash</dt>
          <dd class="mono">{{ summary.content_hash }} <CopyButton :value="summary.content_hash" label="Copy the content hash" /></dd>
        </div>
        <div><dt>Imported</dt><dd>{{ formatTimestamp(summary.created_at) }}</dd></div>
      </dl>

      <form class="filters" aria-label="Filter cases" @submit.prevent>
        <label class="field">
          <span>Tag</span>
          <select class="select" :value="query.tag ?? ''" @change="onTag">
            <option value="">All tags</option>
            <option v-for="tag in tags" :key="tag" :value="tag">{{ tag }}</option>
          </select>
        </label>
        <span class="faint count">{{ plural(cases.length, 'case') }}</span>
      </form>

      <EmptyState v-if="!dataset.cases.length" title="This dataset has no cases" variant="first-run" />
      <EmptyState v-else-if="!cases.length" title="No case has this tag" variant="filtered">
        <template #actions>
          <button type="button" class="btn" @click="set({ tag: null })">Clear filter</button>
        </template>
      </EmptyState>
      <div v-else class="table-wrap panel">
        <table class="data-table cases">
          <caption class="visually-hidden">Cases of {{ datasetRef }}</caption>
          <thead>
            <tr>
              <th scope="col">Case</th>
              <th scope="col">Tags</th>
              <th scope="col">Faults</th>
              <th scope="col">Expects</th>
              <th scope="col"><span class="visually-hidden">Actions</span></th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="c in cases" :key="c.id">
              <th scope="row" class="case">
                <button type="button" class="link mono" @click="set({ case: c.id }, { push: true })">{{ c.id }}</button>
                <span class="task">{{ c.title || c.task }}</span>
              </th>
              <td><span class="chips"><span v-for="tag in c.tags" :key="tag" class="tag">{{ tag }}</span></span></td>
              <td>
                <span v-if="faultLabels(c).length" class="chips">
                  <span v-for="(f, i) in faultLabels(c)" :key="i" class="tag fault">{{ f }}</span>
                </span>
                <span v-else class="faint">—</span>
              </td>
              <td class="expects">{{ expectationSummary(c).join(' · ') || 'completion only' }}</td>
              <td class="num"><button type="button" class="btn" @click="runCase(c)">Run this case</button></td>
            </tr>
          </tbody>
        </table>
      </div>
    </template>

    <Modal :open="openCase !== null" :title="`Case ${openCase?.id ?? ''}`" variant="drawer" @close="set({ case: null })">
      <div v-if="openCase" class="case-detail">
        <p class="task-full">{{ openCase.task }}</p>
        <ul class="expect-list">
          <li v-for="(line, i) in expectationSummary(openCase)" :key="i">{{ line }}</li>
        </ul>
        <JsonView :value="asJson(openCase)" :label="`Case ${openCase.id} as JSON`" />
      </div>
      <template #footer>
        <button v-if="openCase" type="button" class="btn primary" @click="runCase(openCase)">Run this case</button>
      </template>
    </Modal>
  </div>
</template>

<style scoped>
.mono-title {
  font-family: var(--font-mono);
  font-size: 17px;
}
.facts {
  display: flex;
  flex-wrap: wrap;
  gap: 8px 28px;
  margin: 0;
}
.facts dt {
  font-size: 11px;
  color: var(--text-muted);
}
.facts dd {
  display: flex;
  align-items: center;
  gap: 4px;
  margin: 1px 0 0;
  font-size: 13px;
}
.filters {
  display: flex;
  align-items: flex-end;
  gap: 12px;
}
.count {
  font-size: 12.5px;
  padding-bottom: 5px;
}
.case {
  min-width: 220px;
  max-width: 360px;
}
.link {
  padding: 0;
  border: 0;
  background: none;
  color: var(--accent);
  cursor: pointer;
  text-align: left;
}
.link:hover {
  text-decoration: underline;
}
.task {
  display: block;
  font-size: 12.5px;
  font-weight: 400;
  color: var(--text-muted);
}
.chips {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
}
.expects {
  max-width: 380px;
  font-size: 12.5px;
  color: var(--text-muted);
}
.case-detail {
  display: grid;
  gap: 10px;
}
.task-full {
  margin: 0;
  font-size: 14px;
}
.expect-list {
  margin: 0;
  padding-left: 18px;
  font-size: 13px;
  color: var(--text-muted);
}
</style>
