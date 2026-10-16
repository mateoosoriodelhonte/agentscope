<script setup lang="ts">
import { ref } from 'vue'
import { RouterLink, useRouter } from 'vue-router'
import { api } from '@/api/client'
import type { DatasetSummary } from '@/api/types'
import ImportDialog from '@/components/datasets/ImportDialog.vue'
import CopyButton from '@/components/ui/CopyButton.vue'
import EmptyState from '@/components/ui/EmptyState.vue'
import ErrorState from '@/components/ui/ErrorState.vue'
import Skeleton from '@/components/ui/Skeleton.vue'
import { formatTimestamp, shortId } from '@/composables/format'
import { useResource } from '@/composables/useResource'

const router = useRouter()
const datasets = useResource((signal) => api.listDatasets(signal))
const importing = ref(false)

const datasetLink = (d: DatasetSummary) => `/datasets/${encodeURIComponent(d.name)}/${encodeURIComponent(d.version)}`

async function onImported(summary: DatasetSummary): Promise<void> {
  importing.value = false
  await router.push(datasetLink(summary))
}
</script>

<template>
  <div class="screen">
    <header class="screen-head">
      <div>
        <h1 tabindex="-1">Datasets</h1>
        <p class="subtitle">Immutable dataset versions: the same name and version always mean the same cases.</p>
      </div>
      <div class="actions">
        <button type="button" class="btn primary" @click="importing = true">Import dataset</button>
      </div>
    </header>

    <ErrorState v-if="datasets.error.value && !datasets.data.value" :error="datasets.error.value" title="Could not load the datasets" @retry="datasets.reload" />
    <Skeleton v-else-if="!datasets.data.value" :rows="6" />
    <EmptyState v-else-if="!datasets.data.value.length" title="No datasets yet" variant="first-run">
      <p>Import a dataset JSON file, load the demo from the Overview, or import from the command line:</p>
      <p><code>agentscope datasets validate my-dataset.json</code></p>
      <template #actions>
        <button type="button" class="btn primary" @click="importing = true">Import dataset</button>
      </template>
    </EmptyState>
    <div v-else class="table-wrap panel">
      <table class="data-table">
        <caption class="visually-hidden">Dataset versions</caption>
        <thead>
          <tr>
            <th scope="col">Dataset</th>
            <th scope="col">Description</th>
            <th scope="col">Agent</th>
            <th scope="col" class="num">Cases</th>
            <th scope="col">Tags</th>
            <th scope="col">Content hash</th>
            <th scope="col">Imported</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="d in datasets.data.value" :key="`${d.name}@${d.version}`">
            <th scope="row"><RouterLink :to="datasetLink(d)" class="mono">{{ d.name }}@{{ d.version }}</RouterLink></th>
            <td class="description">{{ d.description }}</td>
            <td class="mono">{{ d.agent ?? 'any' }}</td>
            <td class="num mono">{{ d.case_count }}</td>
            <td>
              <span class="tags"><span v-for="tag in d.tags" :key="tag" class="tag">{{ tag }}</span></span>
            </td>
            <td class="mono hash">
              <span :title="d.content_hash">{{ shortId(d.content_hash.replace('sha256:', ''), 12) }}</span>
              <CopyButton :value="d.content_hash" :label="`Copy the content hash of ${d.name}@${d.version}`" />
            </td>
            <td class="faint when">{{ formatTimestamp(d.created_at) }}</td>
          </tr>
        </tbody>
      </table>
    </div>

    <ImportDialog :open="importing" @close="importing = false" @imported="onImported" />
  </div>
</template>

<style scoped>
.description {
  max-width: 360px;
  color: var(--text-muted);
}
.tags {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
}
.hash {
  white-space: nowrap;
}
.when {
  font-size: 12px;
  white-space: nowrap;
}
</style>
