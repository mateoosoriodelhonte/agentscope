import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createMemoryHistory, createRouter, type Router } from 'vue-router'
import { agentsResponse, evalCase, job, V1 } from '@/__tests__/builders'
import { calls, json, mockFetch, type FetchMock } from '@/__tests__/mockFetch'
import { elsewhere } from '@/__tests__/router'
import { closeNewRun, openNewRun, type NewRunPrefill } from '@/composables/newRun'
import NewRunDrawer from '../NewRunDrawer.vue'

let wrapper: VueWrapper | null = null
let router: Router
let fetchMock: FetchMock

beforeEach(async () => {
  fetchMock = mockFetch({
    'GET /api/agents': agentsResponse(),
    'POST /api/runs': () => json({ job_id: 'job-9', kind: 'run', resource_id: 'run-9' }, 202),
    'GET /api/jobs/job-9': job({ job_id: 'job-9', resource_id: 'run-9' }),
  })
  router = createRouter({ history: createMemoryHistory(), routes: [elsewhere] })
  await router.push('/runs')
  wrapper = mount(NewRunDrawer, { global: { plugins: [router] }, attachTo: document.body })
})

afterEach(() => {
  closeNewRun()
  wrapper?.unmount()
  wrapper = null
  vi.unstubAllGlobals()
})

async function openDrawer(prefill: NewRunPrefill = {}): Promise<VueWrapper> {
  openNewRun(prefill)
  await flushPromises()
  return wrapper!
}

const submit = async (drawer: VueWrapper) => {
  await drawer.find('form').trigger('submit')
  await flushPromises()
}

describe('NewRunDrawer', () => {
  it('starts a run of a built-in version with faults, polls the job, and opens the trace', async () => {
    const drawer = await openDrawer({ agentRef: 'research_assistant@v1' })
    expect(drawer.find('[role="dialog"]').exists()).toBe(true)
    expect((drawer.find('select').element as HTMLSelectElement).value).toBe('research_assistant@v1')
    await drawer.find('textarea.task').setValue('  Who founded the library?  ')
    await drawer.findAll('button').find((b) => b.text() === 'Add tool fault')!.trigger('click')
    const row = drawer.find('.fault-row')
    await row.findAll('select')[1]!.setValue('error')
    await row.findAll('input')[1]!.setValue('')
    await drawer.findAll('button').find((b) => b.text() === 'Add model fault')!.trigger('click')
    await submit(drawer)

    const [request] = calls(fetchMock, 'POST', '/api/runs')
    expect(request!.body).toEqual({
      agent_ref: 'research_assistant@v1',
      task: 'Who founded the library?',
      faults: [{ tool: 'search_documents', kind: 'error', on_call: 1, times: null, payload: null }],
      model_faults: [{ kind: 'invalid_json', on_call: 1, times: 1 }],
      expected: null,
    })
    expect(calls(fetchMock, 'GET', '/api/jobs/job-9')).toHaveLength(1)
    expect(router.currentRoute.value.path).toBe('/runs/run-9')
    expect(drawer.find('[role="dialog"]').exists()).toBe(false)
  })

  it('can be closed while the run executes, and links to the run when it finishes', async () => {
    let state: 'running' | 'succeeded' = 'running'
    fetchMock = mockFetch({
      'GET /api/agents': agentsResponse(),
      'POST /api/runs': () => json({ job_id: 'job-9', kind: 'run', resource_id: 'run-9' }, 202),
      'GET /api/jobs/job-9': () => job({ job_id: 'job-9', resource_id: 'run-9', state, finished_at: null }),
    })
    const drawer = await openDrawer({ agentRef: 'research_assistant@v1', task: 'Who founded the library?' })
    await submit(drawer)
    expect(drawer.find('[role="dialog"] .progress').text()).toContain('Running the agent')
    const close = drawer.find('[role="dialog"] .modal-head .close')
    expect(close.attributes('disabled')).toBeUndefined()
    await close.trigger('click')
    expect(drawer.find('[role="dialog"]').exists()).toBe(false)
    expect(drawer.find('.run-notice').text()).toContain('Run in progress')

    // Shown again, it is the same form, and a second run cannot be started meanwhile.
    await drawer.findAll('.run-notice button').find((b) => b.text() === 'Show')!.trigger('click')
    await flushPromises()
    expect((drawer.find('textarea.task').element as HTMLTextAreaElement).value).toBe('Who founded the library?')
    expect(drawer.find('[role="dialog"] .modal-foot .btn.primary').attributes('disabled')).toBeDefined()
    await submit(drawer)
    expect(calls(fetchMock, 'POST', '/api/runs')).toHaveLength(1)
    await drawer.find('[role="dialog"]').trigger('keydown', { key: 'Escape' })
    expect(drawer.find('[role="dialog"]').exists()).toBe(false)

    state = 'succeeded'
    await vi.waitFor(() => expect(drawer.find('.run-notice').text()).toContain('The run finished'), { timeout: 3000 })
    // Closed, the drawer does not navigate away from what the user is looking at.
    expect(router.currentRoute.value.path).toBe('/runs')
    expect(drawer.find('.run-notice a').attributes('href')).toBe('/runs/run-9')
    await drawer.findAll('.run-notice button').find((b) => b.text() === 'Dismiss')!.trigger('click')
    expect(drawer.find('.run-notice').exists()).toBe(false)
  })

  it('reports a run that failed after its drawer was closed', async () => {
    let state: 'running' | 'failed' = 'running'
    fetchMock = mockFetch({
      'GET /api/agents': agentsResponse(),
      'POST /api/runs': () => json({ job_id: 'job-9', kind: 'run', resource_id: 'run-9' }, 202),
      'GET /api/jobs/job-9': () => job({ job_id: 'job-9', resource_id: 'run-9', state, error: state === 'failed' ? 'provider unreachable' : null }),
    })
    const drawer = await openDrawer({ agentRef: 'research_assistant@v1', task: 'Who founded the library?' })
    await submit(drawer)
    await drawer.find('[role="dialog"] .modal-foot .btn').trigger('click')
    expect(drawer.find('[role="dialog"]').exists()).toBe(false)
    state = 'failed'
    await vi.waitFor(() => expect(drawer.find('.run-notice').text()).toContain('The run failed: provider unreachable'), {
      timeout: 3000,
    })
  })

  it('does not show a run that failed in the background as the failure of a newer form', async () => {
    let state: 'running' | 'failed' = 'running'
    fetchMock = mockFetch({
      'GET /api/agents': agentsResponse(),
      'POST /api/runs': () => json({ job_id: 'job-9', kind: 'run', resource_id: 'run-9' }, 202),
      'GET /api/jobs/job-9': () => job({ job_id: 'job-9', resource_id: 'run-9', state, error: state === 'failed' ? 'provider unreachable' : null }),
    })
    await submit(await openDrawer({ agentRef: 'research_assistant@v1', task: 'Who founded the library?' }))
    closeNewRun()
    const drawer = await openDrawer({ agentRef: 'research_assistant@v2', task: 'Another question' })
    expect(drawer.find('[role="dialog"] .progress').text()).toContain('Another run is still in progress')
    state = 'failed'
    await vi.waitFor(() => expect(drawer.find('.run-notice').text()).toContain('The run failed: provider unreachable'), {
      timeout: 3000,
    })
    // The newer form started nothing: it neither claims the failure nor stays blocked.
    expect(drawer.find('[role="dialog"]').text()).not.toContain('Could not start the run')
    expect(drawer.find('[role="dialog"] .modal-foot .btn.primary').attributes('disabled')).toBeUndefined()
  })

  it('lists what to fix instead of sending an invalid form', async () => {
    const drawer = await openDrawer()
    await drawer.findAll('button').find((b) => b.text() === 'Add tool fault')!.trigger('click')
    await drawer.find('.fault-row').findAll('input')[0]!.setValue('99')
    await submit(drawer)
    const alert = drawer.find('[role="alert"]')
    expect(alert.text()).toContain('/task is required')
    expect(alert.text()).toContain('/faults/0/on_call must be a whole number from 1 to 50')
    expect(calls(fetchMock, 'POST', '/api/runs')).toHaveLength(0)
  })

  it('validates a hand-written config before sending it', async () => {
    const drawer = await openDrawer({ agentRef: 'research_assistant@v1' })
    await drawer.findAll('input[type="radio"]')[1]!.trigger('change')
    const editor = drawer.find('textarea.config')
    expect(JSON.parse((editor.element as HTMLTextAreaElement).value)).toEqual(V1)
    await editor.setValue(JSON.stringify({ ...V1, graph: 'fancy', colour: 'blue' }))
    const issues = drawer.find('#config-issues').text()
    expect(issues).toContain('/graph must be one of basic, validated')
    expect(issues).toContain('/colour unknown field')
    await editor.setValue(JSON.stringify({ ...V1, version: 'mine' }))
    expect(drawer.find('#config-issues').text()).toBe('Valid configuration.')
    await drawer.find('textarea.task').setValue('Count to three')
    await submit(drawer)
    const [request] = calls(fetchMock, 'POST', '/api/runs')
    expect(request!.body).toMatchObject({ config: { ...V1, version: 'mine' }, task: 'Count to three' })
    expect(request!.body).not.toHaveProperty('agent_ref')
  })

  it('is prefilled from a dataset case, expected answer included', async () => {
    const c = evalCase({
      faults: [{ tool: 'retrieve_document', kind: 'timeout', on_call: 1, times: 2, payload: null }],
      expected: { field: 'answer', match: 'one_of', value: null, values: ['September', 'Sept'], tolerance: null, max_chars: null },
    })
    const drawer = await openDrawer({
      agent: 'research_assistant',
      task: c.task,
      faults: c.faults,
      modelFaults: c.model_faults,
      expected: c.expected,
      source: 'case qa-1 of research-assistant@1',
    })
    expect(drawer.find('.source').text()).toContain('case qa-1 of research-assistant@1')
    // The latest version of the case's agent is chosen.
    expect((drawer.find('select').element as HTMLSelectElement).value).toBe('research_assistant@v2')
    await submit(drawer)
    const [request] = calls(fetchMock, 'POST', '/api/runs')
    expect(request!.body).toMatchObject({
      task: c.task,
      faults: c.faults,
      expected: { field: 'answer', match: 'one_of', values: ['September', 'Sept'] },
    })
  })

  it('lists the located reasons the server gave for refusing the run', async () => {
    fetchMock.mockImplementation(async (input: string) =>
      input === '/api/agents'
        ? json(agentsResponse())
        : json(
            {
              detail: 'the request is invalid',
              issues: [{ path: '/body/expected/values', message: 'List should have at most 32 items after validation, not 33', severity: 'error' }],
            },
            422,
          ),
    )
    const values = Array.from({ length: 33 }, (_, i) => `v${i}`)
    const drawer = await openDrawer({
      agentRef: 'research_assistant@v1',
      task: 'Which value?',
      expected: { field: 'answer', match: 'one_of', value: null, values, tolerance: null, max_chars: null },
    })
    await submit(drawer)
    const alerts = drawer.findAll('[role="alert"]').map((a) => a.text())
    expect(alerts.some((text) => text.includes('/expected/values List should have at most 32 items after validation, not 33'))).toBe(true)
    expect(alerts.some((text) => text.includes('Could not start the run: the request is invalid'))).toBe(true)
    // The refused field is marked, as it would be for a problem found before sending.
    expect(drawer.find('textarea[aria-invalid="true"]').exists()).toBe(true)
  })

  it('shows why the server refused the run', async () => {
    fetchMock.mockImplementation(async (input: string) =>
      input === '/api/agents'
        ? json(agentsResponse())
        : json({ detail: [{ loc: ['body', 'task'], msg: 'too long', type: 'x' }] }, 422),
    )
    const drawer = await openDrawer()
    await drawer.find('textarea.task').setValue('Hello')
    await submit(drawer)
    expect(drawer.find('[role="alert"]').text()).toContain('Could not start the run: task: too long')
    expect(router.currentRoute.value.path).toBe('/runs')
  })
})
