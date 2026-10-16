<script setup lang="ts">
import { nextTick, watch, watchEffect } from 'vue'
import { RouterView, useRoute, useRouter } from 'vue-router'
import AppSidebar from '@/components/shell/AppSidebar.vue'
import AppTopbar from '@/components/shell/AppTopbar.vue'
import { pageDetail, setPageDetail } from '@/composables/usePageMeta'
import { useTheme } from '@/composables/useTheme'

useTheme()
const route = useRoute()

watchEffect(() => {
  const parts = [pageDetail.value, route.meta.title, 'AgentScope'].filter(Boolean)
  document.title = [...new Set(parts)].join(' · ')
})

/** Views load lazily, so wait a few frames for the page heading to exist. */
async function focusHeading(): Promise<void> {
  await nextTick()
  for (let frame = 0; frame < 30; frame++) {
    const heading = document.querySelector<HTMLElement>('#main h1')
    if (heading) {
      heading.focus()
      return
    }
    await new Promise((resolve) => requestAnimationFrame(resolve))
  }
}

// Query changes (selection, filters) keep focus where it is; only page changes move it.
// The initial load is left alone so the skip link stays the first stop.
void useRouter()
  .isReady()
  .then(() =>
    watch(
      () => route.path,
      () => {
        setPageDetail(null)
        void focusHeading()
      },
    ),
  )
</script>

<template>
  <a class="skip" href="#main">Skip to content</a>
  <div class="shell">
    <AppSidebar />
    <div class="column">
      <AppTopbar />
      <main id="main" class="content" tabindex="-1">
        <RouterView />
      </main>
    </div>
  </div>
</template>

<style scoped>
.shell {
  display: grid;
  grid-template-columns: var(--sidebar-w) minmax(0, 1fr);
  height: 100vh;
  min-width: 1000px;
}
.column {
  display: flex;
  flex-direction: column;
  min-width: 0;
  min-height: 0;
}
.content {
  flex: 1;
  min-height: 0;
  overflow: auto;
}
.content:focus {
  outline: none;
}
.skip {
  position: absolute;
  left: 8px;
  top: -40px;
  z-index: 10;
  padding: 6px 10px;
  border-radius: var(--radius);
  background: var(--accent);
  color: var(--accent-contrast);
}
.skip:focus {
  top: 8px;
}
</style>
