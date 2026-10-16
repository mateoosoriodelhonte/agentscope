import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { defineComponent } from 'vue'
import { createMemoryHistory, createRouter, RouterView, type Router, type RouteRecordRaw } from 'vue-router'

/** Stands in for every page a test does not mount, so links resolve without warnings. */
export const elsewhere: RouteRecordRaw = {
  path: '/:pathMatch(.*)*',
  component: defineComponent({ name: 'Elsewhere', render: () => null }),
}

/** Mounts a RouterView at `path`, waits for the page to load, and returns both. */
export async function mountAt(
  routes: RouteRecordRaw[],
  path: string,
): Promise<{ page: VueWrapper; router: Router }> {
  const router = createRouter({ history: createMemoryHistory(), routes })
  await router.push(path)
  await router.isReady()
  const page = mount(RouterView, { global: { plugins: [router] }, attachTo: document.body })
  await flushPromises()
  return { page, router }
}
