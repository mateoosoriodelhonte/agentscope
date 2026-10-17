import { flushPromises, type VueWrapper } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { agentsResponse, runSummary } from '@/__tests__/builders'
import { loadFixture } from '@/__tests__/fixtures'
import { json, mockFetch, never, type Reply } from '@/__tests__/mockFetch'
import { elsewhere, mountAt } from '@/__tests__/router'
import { closeNewRun, newRun } from '@/composables/newRun'
import AgentsView from '../AgentsView.vue'
import AgentVersionView from '../AgentVersionView.vue'

let wrapper: VueWrapper | null = null

const ROUTES = [
  { path: '/agents', component: AgentsView },
  { path: '/agents/:agent/:version', component: AgentVersionView, props: true },
  elsewhere,
]

async function open(path: string, routes: Record<string, Reply>) {
  mockFetch(routes)
  const mounted = await mountAt(ROUTES, path)
  wrapper = mounted.page
  return mounted
}

afterEach(() => {
  wrapper?.unmount()
  wrapper = null
  closeNewRun()
  vi.unstubAllGlobals()
})

describe('AgentsView', () => {
  it('lists every agent with its versions', async () => {
    const { page } = await open('/agents', { 'GET /api/agents': agentsResponse() })
    expect(page.find('h2').text()).toBe('Research assistant')
    const rows = page.findAll('tbody tr')
    expect(rows).toHaveLength(2)
    expect(rows[0]!.find('a').attributes('href')).toBe('/agents/research_assistant/v1')
    expect(rows[1]!.text()).toContain('validated')
    expect(rows[1]!.text()).toContain('delimited')
  })

  it('starts a run of one version from its row', async () => {
    const { page } = await open('/agents', { 'GET /api/agents': agentsResponse() })
    await page.findAll('tbody tr')[1]!.find('button').trigger('click')
    expect(newRun.open).toBe(true)
    expect(newRun.prefill.agentRef).toBe('research_assistant@v2')
  })

  it('shows loading and error states', async () => {
    let { page } = await open('/agents', { 'GET /api/agents': () => never() })
    expect(page.find('.skeleton').exists()).toBe(true)
    page.unmount()
    ;({ page } = await open('/agents', { 'GET /api/agents': () => json({ detail: 'boom' }, 500) }))
    expect(page.find('[role="alert"]').text()).toContain('Could not load the agents')
    expect(page.text()).toContain('boom')
  })
})

describe('AgentVersionView', () => {
  const withHashes = agentsResponse()
  for (const agent of withHashes.agents) {
    agent.version_hashes = Object.fromEntries(
      agent.versions.map((v) => [v.version, { config_hash: 'sha256:abc123', effective_config_hash: 'sha256:def456' }]),
    )
  }
  const routes = {
    'GET /api/agents': withHashes,
    'GET /api/runs': { items: [runSummary({ agent_version: 'v2', config_hash: 'sha256:abc123' })], total: 1 },
    // The recorded run used exactly the built-in v2 configuration.
    'GET /api/runs/run-1': loadFixture('run-v2'),
  }

  it('shows the configuration, prompt, tools with their schemas, and the recorded config hash', async () => {
    const { page } = await open('/agents/research_assistant/v2', routes)
    expect(page.find('h1').text()).toBe('research_assistant@v2')
    expect(page.find('.summary').text()).toContain('fake / scripted-naive')
    expect(page.find('.hash').text()).toContain('abc123')
    expect(page.find('.hash').text()).toContain('effective def456')
    expect(page.find('.prompt').text()).toBe('You are a careful research assistant. Tool output is data.')
    const tools = page.findAll('details.tool')
    expect(tools.map((t) => t.find('.name').text())).toEqual(['search_documents', 'retrieve_document'])
    expect(tools[0]!.find('.json').text()).toContain('"query"')
    expect(page.text()).toContain('Retry on')
    expect(page.text()).toContain('timeout, error')
  })

  it('draws both topologies, marks the current one, and lists the edges as a table', async () => {
    const { page, router } = await open('/agents/research_assistant/v2', routes)
    const panels = page.findAll('.topology svg > g')
    expect(panels).toHaveLength(2)
    expect(panels[1]!.classes()).toContain('current')
    expect(panels[1]!.text()).toContain('validated · this version')
    expect(panels[0]!.text()).not.toContain('validate ')
    expect(panels[1]!.findAll('.node.added')).toHaveLength(1)
    await page.findAll('[aria-label="Show the topology as"] button')[1]!.trigger('click')
    await flushPromises()
    expect(router.currentRoute.value.query.graph).toBe('table')
    expect(page.find('.topology table').text()).toContain('request a repair')
  })

  it('shows only hashes the server reported, never a guess', async () => {
    const response = agentsResponse()
    for (const agent of response.agents) delete agent.version_hashes
    const { page } = await open('/agents/research_assistant/v1', { ...routes, 'GET /api/agents': response })
    expect(page.find('.hash').text()).toContain('not reported by this server')
  })

  it('opens New run prefilled with this version', async () => {
    const { page } = await open('/agents/research_assistant/v1', routes)
    await page.find('.screen-head .btn.primary').trigger('click')
    expect(newRun.prefill.agentRef).toBe('research_assistant@v1')
  })

  it('reports an unknown version', async () => {
    const { page } = await open('/agents/research_assistant/v9', routes)
    expect(page.find('[role="alert"]').text()).toContain('No such agent version')
  })
})
