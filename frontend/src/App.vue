<script setup lang="ts">
import { RouterLink, RouterView } from 'vue-router'
</script>

<template>
  <div class="shell">
    <header class="topbar">
      <RouterLink to="/" class="brand" aria-label="AgentScope home">
        <svg viewBox="0 0 32 32" width="20" height="20" aria-hidden="true">
          <circle cx="16" cy="16" r="9" fill="none" stroke="currentColor" stroke-width="2.5" />
          <path d="M16 6v4M16 22v4M6 16h4M22 16h4" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" />
        </svg>
        <span>AgentScope</span>
      </RouterLink>
      <nav aria-label="Primary">
        <RouterLink to="/runs">Runs</RouterLink>
      </nav>
    </header>
    <main class="content">
      <RouterView />
    </main>
  </div>
</template>

<style scoped>
.shell {
  min-height: 100vh;
  display: flex;
  flex-direction: column;
}
.topbar {
  display: flex;
  align-items: center;
  gap: 24px;
  padding: 0 20px;
  height: 48px;
  border-bottom: 1px solid var(--border);
  background: var(--surface-1);
}
.brand {
  display: flex;
  align-items: center;
  gap: 8px;
  color: var(--text);
  font-weight: 600;
}
nav a {
  color: var(--text-muted);
}
nav a.router-link-active {
  color: var(--text);
}
.content {
  flex: 1;
  padding: 20px;
}
</style>
