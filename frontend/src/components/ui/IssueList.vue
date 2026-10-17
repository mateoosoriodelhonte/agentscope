<script setup lang="ts">
/*
 * Located problems (a JSON pointer and a message), e.g. the `issues` of a 422: the detail
 * alone only says that a request is invalid, these say why.
 */
defineProps<{ issues: readonly { path: string; message: string }[] }>()
</script>

<template>
  <ul class="issue-list">
    <li v-for="(issue, i) in issues" :key="i"><code>{{ issue.path || '/' }}</code> {{ issue.message }}</li>
  </ul>
</template>

<style scoped>
.issue-list {
  margin: 4px 0 0;
  padding-left: 18px;
  font-size: 12.5px;
}
</style>
