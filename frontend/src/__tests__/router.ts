import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createMemoryHistory, createRouter, RouterView, type Router, type RouteRecordRaw } from 'vue-router'

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
