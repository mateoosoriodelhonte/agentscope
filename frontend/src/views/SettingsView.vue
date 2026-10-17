<script setup lang="ts">
import { computed, ref } from 'vue'
import { api } from '@/api/client'
import type { JsonValue } from '@/api/types'
import PurgeDialog from '@/components/settings/PurgeDialog.vue'
import ErrorState from '@/components/ui/ErrorState.vue'
import RedactedValue from '@/components/ui/RedactedValue.vue'
import Skeleton from '@/components/ui/Skeleton.vue'
import { compact, formatBytes, truncate } from '@/composables/format'
import { useResource } from '@/composables/useResource'

const settings = useResource((signal) => api.settings(signal))
const purging = ref(false)

/** Names that hold credentials. The API already redacts them; this never shows one anyway. */
const SECRET = /(api_?key|private_?key|token|secret|password|passwd|credential|authorization)/i
/** The user-info part of a URL (postgresql://user:password@host/db). */
const URL_CREDENTIALS = /\b([a-z][a-z0-9+.-]*:\/\/)[^\s/?#@"]+@/gi

/** Masks credentials embedded in URLs, again in case the API did not. */
function maskCredentials(text: string): string {
  return text.replace(URL_CREDENTIALS, '$1[REDACTED]@')
}

/** One line per value; credentials are masked before anything is truncated. */
function display(value: JsonValue): string {
  if (value === null || (typeof value !== 'string' && typeof value !== 'object')) return compact(value)
  return truncate(maskCredentials(JSON.stringify(value)), 200)
}

interface Setting {
  key: string
  value: JsonValue
  secret: boolean
}

function flatten(value: { [key: string]: JsonValue }, prefix = ''): Setting[] {
  return Object.entries(value).flatMap(([key, item]) => {
    const path = prefix ? `${prefix}.${key}` : key
    if (item !== null && typeof item === 'object' && !Array.isArray(item) && Object.keys(item).length) {
      return flatten(item, path)
    }
    return [{ key: path, value: item, secret: SECRET.test(key) && item !== null && item !== '' }]
  })
}

const data = computed(() => settings.data.value)
const effective = computed(() => (data.value ? flatten(data.value.effective as { [key: string]: JsonValue }) : []))
const totalRows = computed(() => data.value?.storage.tables.reduce((sum, t) => sum + t.rows, 0) ?? 0)
</script>

<template>
  <div class="screen">
    <header class="screen-head">
      <div>
        <h1 tabindex="-1">Settings</h1>
        <p class="subtitle">
          What this AgentScope instance is configured to do, where it may send data, and what it stores.
          <template v-if="data">Version <span class="mono">{{ data.version }}</span>.</template>
        </p>
      </div>
    </header>

    <ErrorState v-if="settings.error.value && !data" :error="settings.error.value" title="Could not load the settings" @retry="settings.reload" />
    <Skeleton v-else-if="!data" :rows="10" />

    <template v-else>
      <div class="columns">
        <section class="section" aria-labelledby="egress-title">
          <h2 id="egress-title" class="section-title">Network destinations</h2>
          <p class="muted note">Every destination AgentScope may contact. Nothing else leaves this machine.</p>
          <ul v-if="data.egress.length" class="list panel">
            <li v-for="destination in data.egress" :key="destination" class="mono">{{ maskCredentials(destination) }}</li>
          </ul>
          <p v-else class="callout ok">None: AgentScope contacts no network destination.</p>
        </section>
        <section class="section" aria-labelledby="privacy-title">
          <h2 id="privacy-title" class="section-title">Privacy</h2>
          <ul class="list panel privacy">
            <li v-for="statement in data.privacy" :key="statement">{{ statement }}</li>
          </ul>
        </section>
      </div>

      <section class="section" aria-labelledby="storage-title">
        <div class="section-head">
          <h2 id="storage-title" class="section-title">Stored data</h2>
          <div class="actions">
            <button type="button" class="btn danger" @click="purging = true">Purge data…</button>
          </div>
        </div>
        <p class="muted note">
          <span class="mono">{{ data.storage.backend }}</span> at <span class="mono">{{ maskCredentials(data.storage.location) }}</span>
        </p>
        <div class="table-wrap panel">
          <table class="data-table">
            <caption class="visually-hidden">Storage inventory</caption>
            <thead>
              <tr>
                <th scope="col">Table</th>
                <th scope="col" class="num">Rows</th>
                <th scope="col">What it holds</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="table in data.storage.tables" :key="table.name">
                <th scope="row" class="mono">{{ table.name }}</th>
                <td class="num mono">{{ table.rows.toLocaleString() }}</td>
                <td class="muted">{{ table.description }}</td>
              </tr>
            </tbody>
            <tfoot>
              <tr>
                <th scope="row">Total</th>
                <td class="num mono">{{ totalRows.toLocaleString() }}</td>
                <td>
                  Size on disk:
                  <span class="mono">{{ formatBytes(data.storage.size_bytes) ?? 'not measured' }}</span>
                </td>
              </tr>
            </tfoot>
          </table>
        </div>
      </section>

      <section class="section" aria-labelledby="effective-title">
        <h2 id="effective-title" class="section-title">Effective configuration</h2>
        <p class="muted note">Read from <code>AGENTSCOPE_*</code> environment variables at startup. Secrets are never shown.</p>
        <div class="table-wrap panel">
          <table class="data-table">
            <caption class="visually-hidden">Effective configuration</caption>
            <tbody>
              <tr v-for="setting in effective" :key="setting.key">
                <th scope="row" class="mono key">{{ setting.key }}</th>
                <td class="mono value">
                  <RedactedValue v-if="setting.secret" marker="[REDACTED]" />
                  <template v-else>{{ display(setting.value) }}</template>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </section>
    </template>

    <PurgeDialog :open="purging" @close="purging = false" @purged="settings.reload" />
  </div>
</template>

<style scoped>
.columns {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 18px;
  align-items: start;
}
.note {
  margin: 0;
  font-size: 12.5px;
}
.list {
  display: grid;
  gap: 4px;
  margin: 0;
  padding: 10px 12px 10px 28px;
  font-size: 13px;
}
.key {
  width: 40%;
  font-weight: 400;
  color: var(--text-muted);
}
.value {
  overflow-wrap: anywhere;
}
</style>
