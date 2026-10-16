<script setup lang="ts">
import { computed } from 'vue'
import { RouterLink, useRoute } from 'vue-router'
import { pageDetail, pageTrail, type Crumb } from '@/composables/usePageMeta'
import { routes } from '@/router'
import ProviderBadge from './ProviderBadge.vue'
import ThemeToggle from './ThemeToggle.vue'

const route = useRoute()

/** List page › any trail the page set › current page, from the route's sidebar entry. */
const crumbs = computed<Crumb[]>(() => {
  const nav = route.meta.nav
  const list = routes.find((r) => r.name === nav)
  const out: Crumb[] = []
  if (nav && list && list.path !== route.path) {
    out.push({ label: String(list.meta?.title), to: list.path })
  }
  out.push(...pageTrail.value)
  out.push({ label: pageDetail.value ?? route.meta.title ?? 'AgentScope' })
  return out
})
</script>

<template>
  <header class="topbar">
    <nav aria-label="Breadcrumb">
      <ol class="crumbs">
        <li v-for="(crumb, i) in crumbs" :key="i">
          <RouterLink v-if="crumb.to" :to="crumb.to">{{ crumb.label }}</RouterLink>
          <span v-else aria-current="page" class="current">{{ crumb.label }}</span>
        </li>
      </ol>
    </nav>
    <div class="tools">
      <ProviderBadge />
      <ThemeToggle />
    </div>
  </header>
</template>

<style scoped>
.topbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  height: var(--topbar-h);
  padding: 0 16px;
  border-bottom: 1px solid var(--border);
  background: var(--surface-1);
}
.crumbs {
  display: flex;
  align-items: center;
  gap: 6px;
  margin: 0;
  padding: 0;
  list-style: none;
  min-width: 0;
  font-size: 13px;
}
.crumbs li {
  display: flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
}
.crumbs li + li::before {
  content: '/';
  color: var(--text-faint);
}
.crumbs a {
  color: var(--text-muted);
}
.current {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--text);
  font-weight: 500;
}
.tools {
  display: flex;
  align-items: center;
  gap: 10px;
  flex: none;
}
</style>
