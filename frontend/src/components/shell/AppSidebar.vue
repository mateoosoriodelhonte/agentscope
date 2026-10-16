<script setup lang="ts">
import { computed } from 'vue'
import { RouterLink, useRoute } from 'vue-router'
import Icon from '@/components/ui/Icon.vue'
import type { IconName } from '@/components/ui/icons'
import type { NavKey } from '@/router'

interface NavItem {
  key: NavKey
  to: string
  label: string
  icon: IconName
}

const GROUPS: { label: string; items: NavItem[] }[] = [
  {
    label: 'Investigate',
    items: [
      { key: 'overview', to: '/overview', label: 'Overview', icon: 'overview' },
      { key: 'runs', to: '/runs', label: 'Runs', icon: 'runs' },
      { key: 'evaluations', to: '/evaluations', label: 'Evaluations', icon: 'evaluations' },
      { key: 'compare', to: '/compare', label: 'Compare', icon: 'compare' },
      { key: 'failures', to: '/failures', label: 'Failures', icon: 'failures' },
    ],
  },
  {
    label: 'Library',
    items: [
      { key: 'agents', to: '/agents', label: 'Agents', icon: 'agents' },
      { key: 'datasets', to: '/datasets', label: 'Datasets', icon: 'datasets' },
    ],
  },
]
const SETTINGS: NavItem = { key: 'settings', to: '/settings', label: 'Settings', icon: 'settings' }

const route = useRoute()
const current = computed(() => route.meta.nav)
</script>

<template>
  <nav class="sidebar" aria-label="Primary">
    <!-- Custom so the brand never claims aria-current: the Overview entry below does. -->
    <RouterLink v-slot="{ href, navigate }" to="/overview" custom>
      <a :href="href" class="brand" @click="navigate">
        <svg viewBox="0 0 32 32" width="18" height="18" aria-hidden="true">
          <circle cx="16" cy="16" r="9" fill="none" stroke="currentColor" stroke-width="2.5" />
          <path
            d="M16 6v4M16 22v4M6 16h4M22 16h4"
            stroke="currentColor"
            stroke-width="2.5"
            stroke-linecap="round"
          />
        </svg>
        <span>AgentScope</span>
      </a>
    </RouterLink>
    <div class="groups">
      <div v-for="group in GROUPS" :key="group.label" class="group">
        <h2 :id="`nav-${group.label}`" class="group-label">{{ group.label }}</h2>
        <ul :aria-labelledby="`nav-${group.label}`">
          <li v-for="item in group.items" :key="item.key">
            <RouterLink
              :to="item.to"
              class="item"
              :class="{ active: current === item.key }"
              :aria-current="current === item.key ? 'page' : undefined"
            >
              <Icon :name="item.icon" />{{ item.label }}
            </RouterLink>
          </li>
        </ul>
      </div>
    </div>
    <RouterLink
      :to="SETTINGS.to"
      class="item settings"
      :class="{ active: current === SETTINGS.key }"
      :aria-current="current === SETTINGS.key ? 'page' : undefined"
    >
      <Icon :name="SETTINGS.icon" />{{ SETTINGS.label }}
    </RouterLink>
  </nav>
</template>

<style scoped>
.sidebar {
  display: flex;
  flex-direction: column;
  height: 100vh;
  padding: 0 8px 10px;
  border-right: 1px solid var(--border);
  background: var(--surface-1);
}
.brand {
  display: flex;
  align-items: center;
  gap: 8px;
  height: var(--topbar-h);
  padding: 0 8px;
  color: var(--text);
  font-weight: 600;
  letter-spacing: -0.01em;
}
.brand:hover {
  text-decoration: none;
}
.groups {
  flex: 1;
  overflow-y: auto;
  padding-top: 6px;
}
.group + .group {
  margin-top: 14px;
}
.group-label {
  margin: 0 0 4px;
  padding: 0 8px;
  font-size: 11px;
  font-weight: 600;
  letter-spacing: 0.06em;
  text-transform: uppercase;
  color: var(--text-faint);
}
ul {
  margin: 0;
  padding: 0;
  list-style: none;
}
.item {
  display: flex;
  align-items: center;
  gap: 9px;
  height: 30px;
  padding: 0 8px;
  border-radius: var(--radius);
  color: var(--text-muted);
  font-size: 13.5px;
}
.item:hover {
  background: var(--hover);
  color: var(--text);
  text-decoration: none;
}
.item.active {
  background: var(--selected);
  color: var(--text);
  font-weight: 500;
}
.settings {
  border-top: 1px solid var(--border);
  border-radius: 0 0 var(--radius) var(--radius);
  padding-top: 2px;
  margin-top: 8px;
  height: 36px;
}
</style>
