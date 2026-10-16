import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import { createMemoryHistory, createRouter } from 'vue-router'
import AppSidebar from '@/components/shell/AppSidebar.vue'
import { routes } from '@/router'

function makeRouter() {
  return createRouter({ history: createMemoryHistory(), routes })
}

describe('routes', () => {
  it('sends / to the Overview', async () => {
    const router = makeRouter()
    await router.push('/')
    expect(router.currentRoute.value.path).toBe('/overview')
  })

  it.each([
    ['/agents/research_assistant/v2', 'agent-version'],
    ['/datasets/research-assistant/1', 'dataset-version'],
    ['/evaluations/s1', 'evaluation'],
    ['/evaluations/s1/cases/qa-1', 'evaluation-case'],
    ['/compare?a=s1&b=s2', 'compare'],
    ['/compare/runs?a=r1&b=r2', 'compare-runs'],
    ['/failures', 'failures'],
    ['/settings', 'settings'],
  ])('%s is the %s screen', (path, name) => {
    expect(makeRouter().resolve(path).name).toBe(name)
  })

  it('links every sidebar entry to a real screen, Overview first', async () => {
    const router = makeRouter()
    await router.push('/overview')
    const sidebar = mount(AppSidebar, { global: { plugins: [router] } })
    const links = sidebar.findAll('a').map((a) => a.attributes('href')!)
    expect(links[0]).toBe('/overview') // the brand
    expect(links.slice(1)).toEqual(['/overview', '/runs', '/evaluations', '/compare', '/failures', '/agents', '/datasets', '/settings'])
    for (const link of links) expect(router.resolve(link).name).not.toBe('not-found')
    expect(sidebar.find('[aria-current="page"]').text()).toBe('Overview')
  })
})
