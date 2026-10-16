<script setup lang="ts">
import { computed } from 'vue'
import { RouterLink } from 'vue-router'
import type { FailureBucket } from '@/api/types'
import { shortId } from '@/composables/format'

// Failed checks grouped by (check, reason, fault kind, run status), largest first, with runs to open.
const props = defineProps<{ buckets: FailureBucket[] }>()
const sorted = computed(() => [...props.buckets].sort((a, b) => b.count - a.count || a.check.localeCompare(b.check)))
</script>

<template>
  <div class="table-wrap panel">
    <table class="data-table">
      <caption class="visually-hidden">Failure buckets</caption>
      <thead>
        <tr>
          <th scope="col">Failed check</th>
          <th scope="col">Reason</th>
          <th scope="col">Fault kind</th>
          <th scope="col">Run status</th>
          <th scope="col" class="num">Cases</th>
          <th scope="col">Example runs</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="(bucket, i) in sorted" :key="i">
          <th scope="row" class="mono">{{ bucket.check }}</th>
          <td class="mono">{{ bucket.reason }}</td>
          <td class="mono">
            <span v-if="bucket.fault_kind" class="tag fault">{{ bucket.fault_kind }}</span>
            <span v-else class="faint">none</span>
          </td>
          <td class="mono">{{ bucket.run_status.replace('_', ' ') }}</td>
          <td class="num mono">{{ bucket.count }}</td>
          <td class="examples">
            <RouterLink
              v-for="id in bucket.example_run_ids.slice(0, 3)"
              :key="id"
              :to="{ path: `/runs/${encodeURIComponent(id)}`, query: { check: bucket.check } }"
              class="mono"
            >
              {{ shortId(id) }}
            </RouterLink>
            <span v-if="!bucket.example_run_ids.length" class="faint">runs purged</span>
          </td>
        </tr>
      </tbody>
    </table>
  </div>
</template>

<style scoped>
.examples a + a {
  margin-left: 8px;
}
</style>
