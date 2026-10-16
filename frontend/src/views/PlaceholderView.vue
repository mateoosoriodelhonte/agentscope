<script setup lang="ts">
import { RouterLink, useRoute } from 'vue-router'
import EmptyState from '@/components/ui/EmptyState.vue'

const route = useRoute()
</script>

<template>
  <div class="page">
    <h1 tabindex="-1">{{ route.meta.title }}</h1>
    <EmptyState title="This screen arrives in a later release" variant="placeholder">
      <p>{{ route.meta.summary }}</p>
      <p>Runs and their traces are available now.</p>
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
