import { flushPromises, type VueWrapper } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { settingsView } from '@/__tests__/builders'
import { calls, json, mockFetch, never, type Reply } from '@/__tests__/mockFetch'
import { elsewhere, mountAt } from '@/__tests__/router'
import SettingsView from '../SettingsView.vue'

let wrapper: VueWrapper | null = null

async function open(routes: Record<string, Reply>) {
  const fetchMock = mockFetch(routes)
  const mounted = await mountAt([{ path: '/settings', component: SettingsView }, elsewhere], '/settings')
  wrapper = mounted.page
  return { ...mounted, fetchMock }
}

afterEach(() => {
  wrapper?.unmount()
  wrapper = null
  vi.unstubAllGlobals()
})

const button = (page: VueWrapper, text: string) => page.findAll('button').find((b) => b.text() === text)!

describe('SettingsView', () => {
  it('shows effective settings with secrets masked, every destination, privacy and the inventory', async () => {
    const { page } = await open({ 'GET /api/settings': settingsView() })
    const effective = page.find('[aria-labelledby="effective-title"]')
    const key = effective.findAll('tr').find((tr) => tr.text().includes('openai_compatible_api_key'))!
    expect(key.find('.redacted').text()).toBe('[REDACTED]')
    expect(effective.text()).not.toContain('**********')
    expect(effective.text()).toContain('otlp.endpoint')
    expect(page.find('[aria-labelledby="egress-title"]').text()).toContain('127.0.0.1:11434 (Ollama)')
    expect(page.find('.privacy').text()).toContain('Prompts and tool outputs stay on this machine.')
    const storage = page.find('[aria-labelledby="storage-title"]')
    expect(storage.findAll('tbody tr')).toHaveLength(2)
    expect(storage.find('tfoot').text()).toContain('312')
    expect(storage.find('tfoot').text()).toContain('2.5 MiB')
  })

  it('masks credentials embedded in URLs even if the API sent them', async () => {
    const view = settingsView({
      storage: { ...settingsView().storage, backend: 'postgresql', location: 'postgresql://scope:hunter2@db:5432/agentscope' },
      egress: ['https://bot:t0ken@collector.example:4318 (OTLP)'],
    })
    view.effective = {
      ...view.effective,
      database_url: 'postgresql+psycopg://scope:hunter2@db:5432/agentscope',
      mirrors: ['http://a:b@one', 'http://two'],
    }
    const { page } = await open({ 'GET /api/settings': view })
    expect(page.text()).not.toContain('hunter2')
    expect(page.text()).not.toContain('t0ken')
    expect(page.text()).not.toContain('a:b@')
    expect(page.text()).toContain('postgresql+psycopg://[REDACTED]@db:5432/agentscope')
    expect(page.text()).toContain('https://[REDACTED]@collector.example:4318 (OTLP)')
    expect(page.text()).toContain('http://two')
  })

  it('says when nothing may leave the machine', async () => {
    const { page } = await open({ 'GET /api/settings': settingsView({ egress: [] }) })
    expect(page.text()).toContain('None: AgentScope contacts no network destination.')
  })

  it('has loading and error states', async () => {
    let { page } = await open({ 'GET /api/settings': () => never() })
    expect(page.find('.skeleton').exists()).toBe(true)
    page.unmount()
    ;({ page } = await open({ 'GET /api/settings': () => json({ detail: 'no such table: runs' }, 500) }))
    expect(page.find('[role="alert"]').text()).toContain('Database migration pending')
  })
})

describe('purge confirmation', () => {
  const result = { deleted: { runs: 12, spans: 300, datasets: 0 }, size_bytes_before: 2_621_440, size_bytes_after: 1024 }

  it('sends nothing until PURGE is typed exactly, then shows what was deleted', async () => {
    const { page, fetchMock } = await open({ 'GET /api/settings': settingsView(), 'POST /api/settings/purge': result })
    await button(page, 'Purge data…').trigger('click')
    const dialog = page.find('[role="dialog"]')
    const confirm = dialog.find('input.confirm')
    const submit = () => page.find('[role="dialog"] .modal-foot .btn.danger')
    expect(document.activeElement).toBe(confirm.element)
    expect(submit().attributes('disabled')).toBeDefined()
    await confirm.setValue('purge')
    expect(submit().attributes('disabled')).toBeDefined()
    await page.find('[role="dialog"] form').trigger('submit')
    expect(calls(fetchMock, 'POST', '/api/settings/purge')).toHaveLength(0)

    await dialog.find('input[value="evaluations"]').setValue(true)
    await dialog.find('input[type="date"]').setValue('2026-10-01')
    await confirm.setValue('PURGE')
    expect(submit().attributes('disabled')).toBeUndefined()
    await page.find('[role="dialog"] form').trigger('submit')
    // A second submission while the first is in flight is ignored.
    await page.find('[role="dialog"] form').trigger('submit')
    await flushPromises()
    expect(calls(fetchMock, 'POST', '/api/settings/purge').map((c) => c.body)).toEqual([
      { confirm: 'PURGE', scope: 'evaluations', before: '2026-10-01T00:00:00Z' },
    ])
    const shown = page.find('[aria-label="Purge result"]')
    expect(shown.text()).toContain('Purge complete.')
    expect(shown.text()).toContain('runs12 deleted')
    expect(shown.text()).not.toContain('datasets')
    expect(shown.text()).toContain('2.5 MiB → 1.0 KiB')
    // The inventory is reloaded, and another purge needs a new confirmation.
    expect(calls(fetchMock, 'GET', '/api/settings')).toHaveLength(2)
    expect(submit().attributes('disabled')).toBeDefined()
  })

  it('reports a partial purge with its result and offers to purge again', async () => {
    const { page } = await open({
      'GET /api/settings': settingsView(),
      'POST /api/settings/purge': () =>
        json({ detail: { message: 'another connection is reading the database; purge again', result } }, 409),
    })
    await button(page, 'Purge data…').trigger('click')
    await page.find('[role="dialog"] input.confirm').setValue('PURGE')
    await page.find('[role="dialog"] form').trigger('submit')
    await flushPromises()
    const shown = page.find('[aria-label="Purge result"]')
    expect(shown.find('[role="alert"]').text()).toContain('Partially purged.')
    expect(shown.text()).toContain('another connection is reading the database')
    expect(shown.text()).toContain('spans300 deleted')
    expect(page.find('[role="dialog"] .modal-foot .btn.danger').text()).toBe('Purge again')
  })

  it('shows a refused purge as an error', async () => {
    const { page } = await open({
      'GET /api/settings': settingsView(),
      'POST /api/settings/purge': () => json({ detail: 'before must be timezone-aware' }, 422),
    })
    await button(page, 'Purge data…').trigger('click')
    await page.find('[role="dialog"] input.confirm').setValue('PURGE')
    await page.find('[role="dialog"] form').trigger('submit')
    await flushPromises()
    expect(page.find('[role="dialog"] [role="alert"]').text()).toContain('Purge failed: before must be timezone-aware')
  })
})
