<template>
  <kbd class="kbd"><slot /></kbd>
</template>

<style scoped>
.kbd {
  display: inline-block;
  min-width: 18px;
  padding: 0 4px;
  border: 1px solid var(--border-strong);
  border-bottom-width: 2px;
  border-radius: var(--radius-sm);
  background: var(--surface-1);
  color: var(--text);
  font-size: 11px;
  line-height: 16px;
  text-align: center;
}
</style>
