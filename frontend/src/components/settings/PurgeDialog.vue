<script setup lang="ts">
import { computed, ref, shallowRef, watch } from 'vue'
import { api } from '@/api/client'
import { describeError } from '@/api/errors'
import { PURGE_SCOPES, type PurgeRequest, type PurgeResult, type PurgeScope } from '@/api/types'
import Modal from '@/components/ui/Modal.vue'
import { formatBytes } from '@/composables/format'
import { partialPurge, SCOPE_TEXT } from './purge'

/*
 * Deletes stored data. Nothing is sent until "PURGE" is typed exactly. The result shows what
 * was deleted and the storage size before and after; a partial purge (rows deleted, old
 * copies still on disk) says so and offers to purge again.
 */
const props = defineProps<{ open: boolean }>()
const emit = defineEmits<{ close: []; purged: [] }>()

const scope = ref<PurgeScope>('runs')
const before = ref('')
const confirmation = ref('')
const busy = ref(false)
const result = shallowRef<PurgeResult | null>(null)
const partial = ref<string | null>(null)
const error = ref<string | null>(null)

watch(
  () => props.open,
  (open) => {
    if (!open) return
    scope.value = 'runs'
    before.value = ''
    confirmation.value = ''
    result.value = null
    partial.value = null
    error.value = null
  },
)

const confirmed = computed(() => confirmation.value === 'PURGE')
const deleted = computed(() => Object.entries(result.value?.deleted ?? {}).filter(([, n]) => n > 0))

async function purge(): Promise<void> {
  if (!confirmed.value || busy.value) return
  const body: PurgeRequest = {
    confirm: 'PURGE',
    scope: scope.value,
    // A date means "created before the start of that day, UTC".
    before: before.value ? `${before.value}T00:00:00Z` : null,
  }
  busy.value = true
  error.value = null
  partial.value = null
  try {
    result.value = await api.purge(body)
    // Another purge needs a fresh confirmation.
    confirmation.value = ''
    emit('purged')
  } catch (e) {
    const incomplete = partialPurge(e)
    if (incomplete) {
      partial.value = incomplete.message
      result.value = incomplete.result
      confirmation.value = ''
      emit('purged')
    } else {
      error.value = describeError(e).message
    }
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <Modal :open="open" title="Purge stored data" :busy="busy" @close="emit('close')">
    <form id="purge-form" class="purge" novalidate @submit.prevent="purge">
      <fieldset class="scopes" :disabled="busy">
        <legend class="section-title">What to delete</legend>
        <label v-for="s in PURGE_SCOPES" :key="s" class="scope">
          <input v-model="scope" type="radio" name="purge-scope" :value="s" />
          <span>
            <span class="name">{{ s }}</span>
            <span class="text">{{ SCOPE_TEXT[s] }}</span>
          </span>
        </label>
      </fieldset>
      <label class="field">
        <span>Only data created before (optional, UTC)</span>
        <input v-model="before" type="date" class="input date" :disabled="busy" />
      </label>
      <p class="callout warn">
        This cannot be undone. Deleted rows are overwritten and the database is compacted, so the content is gone
        from disk, not just hidden.
      </p>
      <label class="field">
        <span>Type <strong>PURGE</strong> to confirm</span>
        <input v-model="confirmation" class="input confirm" autocomplete="off" spellcheck="false" :disabled="busy" data-autofocus />
      </label>

      <section v-if="result" class="result" :class="{ partial }" aria-live="polite" aria-label="Purge result">
        <p v-if="partial" class="callout warn" role="alert">
          <span><strong>Partially purged.</strong> {{ partial }}</span>
        </p>
        <p v-else class="callout ok" role="status">Purge complete.</p>
        <table class="data-table panel">
          <caption class="visually-hidden">Rows deleted per table</caption>
          <tbody>
            <tr v-for="[table, n] in deleted" :key="table">
              <th scope="row" class="mono">{{ table }}</th>
              <td class="num mono">{{ n.toLocaleString() }} deleted</td>
            </tr>
            <tr v-if="!deleted.length">
              <td colspan="2" class="faint">Nothing matched: no rows were deleted.</td>
            </tr>
            <tr>
              <th scope="row">Storage size</th>
              <td class="num mono">
                {{ formatBytes(result.size_bytes_before) ?? 'not measured' }} → {{ formatBytes(result.size_bytes_after) ?? 'not measured' }}
              </td>
            </tr>
          </tbody>
        </table>
      </section>
      <p v-else-if="partial" class="callout warn" role="alert">
        <span><strong>Partially purged.</strong> {{ partial }}</span>
      </p>
      <p v-if="error" class="callout fail" role="alert">Purge failed: {{ error }}</p>
    </form>
    <template #footer>
      <button type="button" class="btn" :disabled="busy" @click="emit('close')">{{ result ? 'Close' : 'Cancel' }}</button>
      <button type="submit" form="purge-form" class="btn danger" :disabled="busy || !confirmed">
        {{ busy ? 'Purging…' : partial ? 'Purge again' : 'Purge' }}
      </button>
    </template>
  </Modal>
</template>

<style scoped>
.purge {
  display: grid;
  gap: 12px;
}
.scopes {
  display: grid;
  gap: 6px;
  margin: 0;
  padding: 0;
  border: 0;
}
legend {
  margin-bottom: 6px;
  padding: 0;
}
.scope {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  padding: 6px 8px;
  border: 1px solid var(--border);
  border-radius: var(--radius);
  cursor: pointer;
  font-size: 13px;
}
.scope:has(input:checked) {
  border-color: var(--accent);
  background: var(--accent-soft);
}
.scope input {
  margin-top: 3px;
}
.name {
  display: block;
  font-family: var(--font-mono);
  font-size: 12.5px;
  font-weight: 600;
}
.text {
  display: block;
  color: var(--text-muted);
  font-size: 12.5px;
}
.date {
  width: 180px;
}
.confirm {
  width: 180px;
  font-family: var(--font-mono);
}
.result {
  display: grid;
  gap: 8px;
}
</style>
