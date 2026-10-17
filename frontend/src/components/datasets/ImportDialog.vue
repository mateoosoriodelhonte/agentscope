<script setup lang="ts">
import { computed, ref, shallowRef, watch } from 'vue'
import { api } from '@/api/client'
import { describeError } from '@/api/errors'
import type { DatasetSummary, DatasetValidation } from '@/api/types'
import Modal from '@/components/ui/Modal.vue'
import StatusBadge from '@/components/ui/StatusBadge.vue'
import { formatBytes, plural } from '@/composables/format'
import { parseJson, type FieldIssue } from '@/composables/parseJson'

/*
 * Import a dataset version in two steps: validate (lint without importing), then import.
 * A picked file is read in the browser with FileReader; the only request that carries it
 * is the POST to this machine's AgentScope API. Both requests send the text exactly as it
 * is: the server's strict loader (duplicate keys, non-finite numbers) must see what the
 * reviewer saw, so the browser parses it only to point out a syntax error early.
 */
const props = defineProps<{ open: boolean }>()
const emit = defineEmits<{ close: []; imported: [summary: DatasetSummary] }>()

/** The API's default request limit for dataset bodies (max_request_bytes). */
const MAX_BYTES = 5 * 1024 * 1024
const source = ref('')
const fileName = ref<string | null>(null)
const parseIssues = ref<FieldIssue[]>([])
const validation = shallowRef<DatasetValidation | null>(null)
/** The exact text that was validated; editing it again requires a new validation. */
const validatedSource = ref<string | null>(null)
const busy = ref(false)
const error = ref<string | null>(null)

watch(
  () => props.open,
  (open) => {
    if (!open) return
    source.value = ''
    fileName.value = null
    parseIssues.value = []
    validation.value = null
    validatedSource.value = null
    error.value = null
  },
)

watch(source, (text) => {
  if (validatedSource.value !== null && text !== validatedSource.value) {
    validation.value = null
    validatedSource.value = null
  }
})

function readFile(event: Event): void {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  if (!file) return
  error.value = null
  if (file.size > MAX_BYTES) {
    error.value = `${file.name} is ${formatBytes(file.size)}; the limit is ${formatBytes(MAX_BYTES)}.`
    return
  }
  const reader = new FileReader()
  reader.onload = () => {
    source.value = typeof reader.result === 'string' ? reader.result : ''
    fileName.value = file.name
  }
  reader.onerror = () => {
    error.value = `Could not read ${file.name}.`
  }
  reader.readAsText(file)
}

const issues = computed(() => {
  const list = validation.value?.issues ?? []
  return [...list].sort((a, b) => (a.severity === b.severity ? 0 : a.severity === 'error' ? -1 : 1))
})
const canImport = computed(() => validation.value?.valid === true && validatedSource.value === source.value)

/** The body is the text as UTF-8, so that is what counts against the server's limit. */
function tooLarge(text: string): string | null {
  const bytes = new TextEncoder().encode(text).length
  return bytes > MAX_BYTES ? `The dataset is ${formatBytes(bytes)}; the limit is ${formatBytes(MAX_BYTES)}.` : null
}

async function validate(): Promise<void> {
  error.value = null
  validation.value = null
  const text = source.value
  parseIssues.value = parseJson(text).issues
  if (parseIssues.value.length) return
  error.value = tooLarge(text)
  if (error.value) return
  busy.value = true
  try {
    validation.value = await api.validateDataset(text)
    validatedSource.value = text
  } catch (e) {
    error.value = describeError(e).message
  } finally {
    busy.value = false
  }
}

async function importDataset(): Promise<void> {
  if (!canImport.value) return
  error.value = null
  busy.value = true
  try {
    // The validated text itself: canImport guarantees it has not changed since.
    const summary = await api.importDataset(source.value)
    emit('imported', summary)
  } catch (e) {
    const described = describeError(e)
    error.value =
      described.status === 409
        ? `${described.message}. Versions are immutable: give the changed dataset a new version.`
        : described.message
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <Modal :open="open" title="Import a dataset" width="min(720px, calc(100vw - 32px))" :busy="busy" @close="emit('close')">
    <div class="import">
      <p class="note">
        Paste the dataset JSON or choose a file. The file is read in this browser and sent only to the local AgentScope
        API, first to validate it and then, if you choose, to import it.
      </p>
      <label class="field">
        <span>Dataset file</span>
        <input type="file" accept="application/json,.json" class="file" @change="readFile" />
      </label>
      <label class="field">
        <span>Dataset JSON{{ fileName ? ` (from ${fileName})` : '' }}</span>
        <textarea
          v-model="source"
          class="textarea"
          rows="12"
          spellcheck="false"
          data-autofocus
          :aria-invalid="parseIssues.length ? 'true' : undefined"
        ></textarea>
      </label>

      <div v-if="parseIssues.length" class="callout fail" role="alert">
        <div>
          <strong>Not valid JSON.</strong>
          <ul class="issues">
            <li v-for="issue in parseIssues" :key="issue.message"><code>{{ issue.path }}</code> {{ issue.message }}</li>
          </ul>
        </div>
      </div>

      <section v-if="validation" class="result" aria-live="polite" aria-label="Validation result">
        <p class="verdict">
          <StatusBadge :status="validation.valid ? 'passed' : 'failed'" />
          <span v-if="validation.valid">
            Valid: {{ plural(validation.case_count, 'case') }}<template v-if="validation.content_hash">,
              <span class="mono">{{ validation.content_hash }}</span></template>
          </span>
          <span v-else>{{ plural(issues.filter((i) => i.severity === 'error').length, 'error') }} to fix before importing</span>
        </p>
        <table v-if="issues.length" class="data-table panel">
          <caption class="visually-hidden">Validation issues by JSON pointer</caption>
          <thead>
            <tr>
              <th scope="col">Location</th>
              <th scope="col">Issue</th>
              <th scope="col">Severity</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="(issue, i) in issues" :key="i">
              <td class="mono pointer">{{ issue.path || '/' }}</td>
              <td>{{ issue.message }}</td>
              <td>
                <StatusBadge :outcome="issue.severity === 'error' ? { tone: 'fail', label: 'error' } : { tone: 'warn', label: 'warning' }" variant="plain" />
              </td>
            </tr>
          </tbody>
        </table>
      </section>

      <p v-if="error" class="callout fail" role="alert">{{ error }}</p>
    </div>
    <template #footer>
      <button type="button" class="btn" :disabled="busy || !source.trim()" @click="validate">Validate</button>
      <button type="button" class="btn primary" :disabled="busy || !canImport" @click="importDataset">Import</button>
    </template>
  </Modal>
</template>

<style scoped>
.import {
  display: grid;
  gap: 12px;
}
.note {
  margin: 0;
  font-size: 12.5px;
  color: var(--text-muted);
}
.file {
  font-size: 12.5px;
  color: var(--text);
}
.issues {
  margin: 4px 0 0;
  padding-left: 18px;
  font-size: 12.5px;
}
.result {
  display: grid;
  gap: 8px;
}
.verdict {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  margin: 0;
  font-size: 13px;
}
.pointer {
  white-space: nowrap;
}
</style>
