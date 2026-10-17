import { flushPromises, type VueWrapper } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { dataset, datasetSummary, evalCase } from '@/__tests__/builders'
import { calls, json, mockFetch, never, type Reply } from '@/__tests__/mockFetch'
import { elsewhere, mountAt } from '@/__tests__/router'
import type { DatasetValidation } from '@/api/types'
import { closeNewRun, newRun } from '@/composables/newRun'
import DatasetsView from '../DatasetsView.vue'
import DatasetView from '../DatasetView.vue'

let wrapper: VueWrapper | null = null

const ROUTES = [
  { path: '/datasets', component: DatasetsView },
  { path: '/datasets/:name/:version', component: DatasetView, props: true },
  elsewhere,
]

async function open(path: string, routes: Record<string, Reply>) {
  const fetchMock = mockFetch(routes)
  const mounted = await mountAt(ROUTES, path)
  wrapper = mounted.page
  return { ...mounted, fetchMock }
}

afterEach(() => {
  wrapper?.unmount()
  wrapper = null
  closeNewRun()
  vi.unstubAllGlobals()
})

const button = (page: VueWrapper, text: string) => page.findAll('button').find((b) => b.text() === text)!

describe('DatasetsView', () => {
  it('lists dataset versions', async () => {
    const { page } = await open('/datasets', { 'GET /api/datasets': [datasetSummary()] })
    const row = page.find('tbody tr')
    expect(row.find('a').attributes('href')).toBe('/datasets/research-assistant/1')
    expect(row.text()).toContain('qa')
  })

  it('has loading, first-run and error states', async () => {
    let { page } = await open('/datasets', { 'GET /api/datasets': () => never() })
    expect(page.find('.skeleton').exists()).toBe(true)
    page.unmount()
    ;({ page } = await open('/datasets', { 'GET /api/datasets': [] }))
    expect(page.text()).toContain('No datasets yet')
    page.unmount()
    ;({ page } = await open('/datasets', { 'GET /api/datasets': new TypeError('Failed to fetch') }))
    expect(page.find('[role="alert"]').text()).toContain('Cannot reach the AgentScope API')
  })
})

describe('dataset import', () => {
  const document = JSON.stringify(dataset())
  const invalid: DatasetValidation = {
    valid: false,
    content_hash: null,
    case_count: 1,
    issues: [
      { path: '/cases/0/expected/match', message: "Input should be 'exact', 'normalized', …", severity: 'error' },
      { path: '/cases/0/tags', message: 'no tags', severity: 'warning' },
    ],
  }
  const valid: DatasetValidation = { valid: true, content_hash: 'sha256:abc', case_count: 1, issues: [] }

  it('validates first, lists issues by JSON pointer, then imports and opens the dataset', async () => {
    let validation = invalid
    const { page, router, fetchMock } = await open('/datasets', {
      'GET /api/datasets': [],
      'POST /api/datasets/validate': () => validation,
      'POST /api/datasets': () => json(datasetSummary(), 201),
    })
    await button(page, 'Import dataset').trigger('click')
    const dialog = page.find('[role="dialog"]')
    const importButton = () => button(page, 'Import')

    await dialog.find('textarea').setValue('{"name": ')
    await button(page, 'Validate').trigger('click')
    await flushPromises()
    expect(page.find('[role="dialog"] [role="alert"]').text()).toContain('Not valid JSON')
    expect(calls(fetchMock, 'POST', '/api/datasets/validate')).toHaveLength(0)

    await dialog.find('textarea').setValue(document)
    await button(page, 'Validate').trigger('click')
    await flushPromises()
    const rows = page.findAll('[role="dialog"] tbody tr')
    expect(rows.map((r) => r.find('td').text())).toEqual(['/cases/0/expected/match', '/cases/0/tags'])
    expect(rows[0]!.text()).toContain('error')
    expect(rows[1]!.text()).toContain('warning')
    expect(importButton().attributes('disabled')).toBeDefined()
    expect(calls(fetchMock, 'POST', '/api/datasets/validate')[0]!.body).toEqual(JSON.parse(document))

    validation = valid
    await button(page, 'Validate').trigger('click')
    await flushPromises()
    expect(page.find('[role="dialog"]').text()).toContain('Valid: 1 case')
    // Editing after validation requires validating again.
    await dialog.find('textarea').setValue(`${document} `)
    expect(importButton().attributes('disabled')).toBeDefined()
    await button(page, 'Validate').trigger('click')
    await flushPromises()
    await importButton().trigger('click')
    await flushPromises()
    expect(calls(fetchMock, 'POST', '/api/datasets')[0]!.body).toEqual(JSON.parse(document))
    expect(router.currentRoute.value.path).toBe('/datasets/research-assistant/1')
  })

  it('validates and imports the exact text, so the server rejects what JSON.parse would hide', async () => {
    // A duplicate key (the last one would win) and a number JSON.parse turns into Infinity.
    const text =
      '{"schema_version": 1, "name": "qa", "cases": [{"id": "c1", "task": "t",\n' +
      '  "forbidden_tools": ["write_note"], "forbidden_tools": [],\n' +
      '  "expected": {"field": "answer", "match": "numeric", "value": 1, "tolerance": 1e400}}]}\n'
    const { page, fetchMock } = await open('/datasets', {
      'GET /api/datasets': [],
      'POST /api/datasets/validate': valid,
      'POST /api/datasets': () => json(datasetSummary(), 201),
    })
    await button(page, 'Import dataset').trigger('click')
    await page.find('[role="dialog"] textarea').setValue(text)
    await button(page, 'Validate').trigger('click')
    await flushPromises()
    await button(page, 'Import').trigger('click')
    await flushPromises()
    const sent = fetchMock.mock.calls.filter(([, init]) => init?.method === 'POST').map(([url, init]) => [url, init!.body])
    expect(sent).toEqual([
      ['/api/datasets/validate', text],
      ['/api/datasets', text],
    ])
  })

  it('refuses a document over the server limit (5 MiB) before sending it', async () => {
    const { page, fetchMock } = await open('/datasets', { 'GET /api/datasets': [], 'POST /api/datasets/validate': valid })
    await button(page, 'Import dataset').trigger('click')
    // Multi-byte characters: the limit is on the UTF-8 bytes sent, not on characters.
    const text = `{"description": "${'é'.repeat(2.5 * 1024 * 1024)}"}`
    await page.find('[role="dialog"] textarea').setValue(text)
    await button(page, 'Validate').trigger('click')
    await flushPromises()
    expect(page.find('[role="dialog"] [role="alert"]').text()).toContain('the limit is 5.0 MiB')
    expect(calls(fetchMock, 'POST', '/api/datasets/validate')).toHaveLength(0)

    const input = page.find('input[type="file"]')
    const file = new File(['x'.repeat(5 * 1024 * 1024 + 1)], 'big.json', { type: 'application/json' })
    Object.defineProperty(input.element, 'files', { value: [file] })
    await input.trigger('change')
    expect(page.find('[role="dialog"] [role="alert"]').text()).toContain('big.json is 5.0 MiB; the limit is 5.0 MiB')
  })

  it('reads a picked file in the browser', async () => {
    const { page } = await open('/datasets', { 'GET /api/datasets': [] })
    await button(page, 'Import dataset').trigger('click')
    const input = page.find('input[type="file"]')
    const file = new File([document], 'qa.json', { type: 'application/json' })
    Object.defineProperty(input.element, 'files', { value: [file] })
    await input.trigger('change')
    await vi.waitFor(() => expect((page.find('[role="dialog"] textarea').element as HTMLTextAreaElement).value).toBe(document))
    expect(page.find('[role="dialog"]').text()).toContain('from qa.json')
  })

  it('refuses a picked file that is not valid UTF-8 instead of sending replaced characters', async () => {
    const { page, fetchMock } = await open('/datasets', { 'GET /api/datasets': [], 'POST /api/datasets/validate': valid })
    await button(page, 'Import dataset').trigger('click')
    const input = page.find('input[type="file"]')
    // "Café" in Latin-1: byte 0xE9 is not UTF-8. Decoded leniently it would become "Caf�".
    const bytes = new Uint8Array([...new TextEncoder().encode('{"name": "Caf'), 0xe9, ...new TextEncoder().encode('"}')])
    const file = new File([bytes], 'latin1.json', { type: 'application/json' })
    Object.defineProperty(input.element, 'files', { value: [file] })
    await input.trigger('change')
    await vi.waitFor(() =>
      expect(page.find('[role="dialog"] [role="alert"]').text()).toContain('latin1.json is not valid UTF-8 text'),
    )
    expect((page.find('[role="dialog"] textarea').element as HTMLTextAreaElement).value).toBe('')
    expect(calls(fetchMock, 'POST', '/api/datasets/validate')).toHaveLength(0)
  })

  it('explains a version conflict', async () => {
    const { page } = await open('/datasets', {
      'GET /api/datasets': [],
      'POST /api/datasets/validate': valid,
      'POST /api/datasets': () => json({ detail: 'research-assistant@1 already exists with different content' }, 409),
    })
    await button(page, 'Import dataset').trigger('click')
    await page.find('[role="dialog"] textarea').setValue(document)
    await button(page, 'Validate').trigger('click')
    await flushPromises()
    await button(page, 'Import').trigger('click')
    await flushPromises()
    expect(page.find('[role="dialog"] [role="alert"]').text()).toContain('Versions are immutable')
  })
})

describe('DatasetView', () => {
  const cases = [
    evalCase(),
    evalCase({
      id: 'fault-timeout',
      title: '',
      tags: ['fault'],
      expected: null,
      expected_tool_calls: [],
      forbidden_tools: [],
      faults: [{ tool: 'search_documents', kind: 'timeout', on_call: 1, times: 1, payload: null }],
      success_criteria: { must_complete: true, expected_output_status: null, expect_recovery: true, max_steps_budget: null, max_tool_calls: null },
    }),
  ]
  const routes = {
    'GET /api/datasets/research-assistant/1': { summary: datasetSummary({ case_count: 2 }), dataset: dataset(cases) },
  }

  it('shows cases with tags, faults and what they expect', async () => {
    const { page } = await open('/datasets/research-assistant/1', routes)
    expect(page.find('h1').text()).toBe('research-assistant@1')
    const rows = page.findAll('tbody tr')
    expect(rows[0]!.text()).toContain('answer normalized "1987"')
    expect(rows[0]!.text()).toContain('forbids send_email')
    expect(rows[1]!.find('.tag.fault').text()).toBe('search_documents: timeout')
    expect(rows[1]!.text()).toContain('must recover')
    expect(page.find('a[download]').attributes('href')).toBe('/api/datasets/research-assistant/1/export')
    expect(page.findAll('a').find((a) => a.text() === 'Evaluate this dataset')!.attributes('href')).toBe(
      '/evaluations?new=research-assistant@1',
    )
  })

  it('filters by tag through the URL', async () => {
    const { page, router } = await open('/datasets/research-assistant/1', routes)
    await page.find('select').setValue('fault')
    await flushPromises()
    expect(router.currentRoute.value.query.tag).toBe('fault')
    expect(page.findAll('tbody tr')).toHaveLength(1)
  })

  it('opens a case drawer from the URL and runs the case from it', async () => {
    const { page, router } = await open('/datasets/research-assistant/1?case=fault-timeout', routes)
    const drawer = page.find('[role="dialog"]')
    expect(drawer.text()).toContain('Case fault-timeout')
    expect(drawer.find('.json').text()).toContain('"kind": "timeout"')
    await drawer.find('.modal-foot button').trigger('click')
    await flushPromises()
    expect(router.currentRoute.value.query.case).toBeUndefined()
    expect(newRun.open).toBe(true)
    expect(newRun.prefill).toMatchObject({
      agent: 'research_assistant',
      task: cases[1]!.task,
      faults: cases[1]!.faults,
      source: 'case fault-timeout of research-assistant@1',
    })
  })

  it('reports a missing dataset', async () => {
    const { page } = await open('/datasets/nope/1', routes)
    expect(page.find('[role="alert"]').text()).toContain('Could not load this dataset')
  })
})
