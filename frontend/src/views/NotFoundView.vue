<script setup lang="ts">
import { RouterLink, useRoute } from 'vue-router'
import EmptyState from '@/components/ui/EmptyState.vue'

const route = useRoute()
</script>

<template>
  <div class="page">
    <h1 tabindex="-1">Page not found</h1>
    <EmptyState title="Nothing lives at this address" variant="error">
      <p>
        <code>{{ route.path }}</code> is not a page in this dashboard. The link may be from a newer
        version of AgentScope.
      </p>
      <template #actions>
        <RouterLink to="/runs" class="btn">Go to Runs</RouterLink>
      </template>
    </EmptyState>
  </div>
</template>

<style scoped>
.page {
  padding: 20px 24px;
}
.btn:hover {
  text-decoration: none;
}
</style>
