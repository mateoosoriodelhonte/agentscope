import { flushPromises, type DOMWrapper, type VueWrapper } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ablation, ablationItem, agentsResponse, comparison, datasetSummary, job, suiteItem } from '@/__tests__/builders'
import { calls, json, mockFetch, never, paged, type Reply } from '@/__tests__/mockFetch'
import { elsewhere, mountAt } from '@/__tests__/router'
import type { Ablation, Comparison, ConfigDifference, ConfigDimension } from '@/api/types'
import CompareView from '@/views/CompareView.vue'

let wrapper: VueWrapper | null = null

/** The v1 -> v2 comparison of the ablation builder: four ablatable dimensions changed. */
function attributed(overrides: Partial<Comparison> = {}): Comparison {
  return comparison({
    config_diff: [
      { path: 'prompt_version', dimension: 'prompt', a: 'p1', b: 'p2' },
      { path: 'graph', dimension: 'graph', a: 'basic', b: 'validated' },
      { path: 'isolate_untrusted', dimension: 'isolation', a: false, b: true },
      { path: 'retry.max_tool_retries', dimension: 'retry', a: 0, b: 1 },
      { path: 'environment.agentscope_version', dimension: 'other', a: '1.0.0', b: '1.0.1' },
    ],
    ...overrides,
  })
}

const BASE: Record<string, Reply> = {
  'GET /api/evaluations': [suiteItem()],
  'GET /api/experiments': [],
  'GET /api/datasets': [datasetSummary()],
  'GET /api/agents': agentsResponse(),
  'GET /api/experiments/exp-1': attributed(),
}

/** A comparison whose ablation is stored. */
function stored(data: Ablation = ablation()): Record<string, Reply> {
  return { 'GET /api/ablations': paged([ablationItem()]), 'GET /api/ablations/abl-1': data }
}

async function open(query: string, routes: Record<string, Reply> = {}) {
  const fetchMock = mockFetch({ ...BASE, ...routes })
  const mounted = await mountAt([{ path: '/compare', component: CompareView }, elsewhere], `/compare?experiment=exp-1${query}`)
  wrapper = mounted.page
  return { ...mounted, fetchMock, panel: () => mounted.page.find('#attribution') }
}

afterEach(() => {
  wrapper?.unmount()
  wrapper = null
  vi.unstubAllGlobals()
  vi.useRealTimers()
})

const hrefs = (el: DOMWrapper<Element>) => el.findAll('a').map((a) => a.attributes('href'))

describe('AttributionPanel', () => {
  it('follows the configuration differences and looks for a stored ablation of this comparison', async () => {
    const { page, panel, fetchMock } = await open('', stored())
    const sections = page.findAll('section[aria-labelledby]').map((s) => s.attributes('aria-labelledby'))
    expect(sections.indexOf('attribution-title')).toBe(sections.indexOf('config-diff-title') + 1)
    expect(calls(fetchMock, 'GET', '/api/ablations')[0]!.url.search).toBe('?experiment_id=exp-1&limit=1')
    expect(panel().find('h2').text()).toBe('Attribution')
    expect(calls(fetchMock, 'POST', '/api/ablations')).toHaveLength(0)
  })

  it('offers to attribute the change, saying what it will run', async () => {
    const { panel } = await open('', { 'GET /api/ablations': paged([]) })
    const offer = panel().find('.callout.start')
    expect(offer.text()).toContain(
      'Attribution evaluates research-assistant@1 again with one dimension changed at a time, so each of the 4 changed dimensions gets its own effect: 2 × 4 variant evaluations, plus 6 pairs (4 choose 2) to locate interactions.',
    )
    expect(offer.find('button.primary').text()).toBe('Attribute the change')
  })

  it('says when there are too many dimensions to evaluate pairs', async () => {
    const many: ConfigDifference[] = [
      ['prompt_version', 'prompt'],
      ['model.model', 'model'],
      ['tools', 'tools'],
      ['tool_schema_version', 'tool_schema'],
      ['graph', 'graph'],
      ['isolate_untrusted', 'isolation'],
      ['retry.max_tool_retries', 'retry'],
    ].map(([path, dimension]) => ({ path: path!, dimension: dimension as ConfigDimension, a: 1, b: 2 }))
    const { panel } = await open('', {
      'GET /api/experiments/exp-1': attributed({ config_diff: many }),
      'GET /api/ablations': paged([]),
    })
    expect(panel().find('.callout.start').text()).toContain('2 × 7 variant evaluations (pairs are only evaluated up to 6 dimensions).')
  })

  it('starts the ablation, polls its job with progress, then shows the result', async () => {
    let state: 'running' | 'succeeded' = 'running'
    const { panel, fetchMock } = await open('', {
      'GET /api/ablations': paged([]),
      'POST /api/ablations': () => json({ job_id: 'job-a', kind: 'ablation', resource_id: 'abl-1' }, 202),
      'GET /api/jobs/job-a': () =>
        job({ job_id: 'job-a', kind: 'ablation', resource_id: 'abl-1', state, progress: state === 'running' ? 12 : 60, total: 60, finished_at: null }),
      'GET /api/ablations/abl-1': ablation(),
    })
    await panel().find('.callout.start button').trigger('click')
    await flushPromises()
    expect(calls(fetchMock, 'POST', '/api/ablations').map((c) => c.body)).toEqual([{ experiment_id: 'exp-1' }])
    const status = panel().find('[role="status"]')
    expect(status.text()).toContain('Attributing the change… 12/60')
    const bar = status.find('progress').element as HTMLProgressElement
    expect([bar.value, bar.max]).toEqual([12, 60])
    expect(bar.getAttribute('aria-label')).toBe('Attribution progress')

    state = 'succeeded'
    await vi.waitFor(() => expect(panel().find('.summary').exists()).toBe(true), { timeout: 3000 })
    expect(panel().find('[role="status"]').exists()).toBe(false)
    expect(calls(fetchMock, 'GET', '/api/ablations/abl-1')).toHaveLength(1)
  })

  it('shows an indeterminate progress bar before the job reports a total', async () => {
    const { panel } = await open('', {
      'GET /api/ablations': paged([]),
      'POST /api/ablations': () => never(),
    })
    await panel().find('.callout.start button').trigger('click')
    expect(panel().find('[role="status"]').text()).toContain('Attributing the change… starting')
    expect(panel().find('progress').attributes('value')).toBeUndefined()
  })

  it('says why the server refused to start, issue by issue, and can try again', async () => {
    const { panel, fetchMock } = await open('', {
      'GET /api/ablations': paged([]),
      'POST /api/ablations': () =>
        json(
          {
            detail: 'the comparison has no dimension a configuration field expresses',
            issues: [{ path: '/body/experiment_id', message: 'only environment differences', severity: 'error' }],
          },
          422,
        ),
    })
    await panel().find('.callout.start button').trigger('click')
    await flushPromises()
    const alert = panel().find('[role="alert"]')
    expect(alert.text()).toContain('Could not attribute the change: the comparison has no dimension a configuration field expresses')
    expect(alert.findAll('li').map((li) => li.text())).toEqual(['/experiment_id only environment differences'])
    await alert.find('button').trigger('click')
    await flushPromises()
    expect(calls(fetchMock, 'POST', '/api/ablations')).toHaveLength(2)
  })

  it('reports a job that failed', async () => {
    const { panel } = await open('', {
      'GET /api/ablations': paged([]),
      'POST /api/ablations': () => json({ job_id: 'job-a', kind: 'ablation', resource_id: 'abl-1' }, 202),
      'GET /api/jobs/job-a': job({ job_id: 'job-a', kind: 'ablation', state: 'failed', error: 'provider unreachable' }),
    })
    await panel().find('.callout.start button').trigger('click')
    await flushPromises()
    expect(panel().find('[role="alert"]').text()).toContain('Could not attribute the change: provider unreachable')
    expect(panel().find('.summary').exists()).toBe(false)
  })

  it('reports a lookup that failed, with a retry', async () => {
    let fail = true
    const { panel } = await open('', {
      'GET /api/ablations': () => (fail ? json({ detail: 'database is locked' }, 500) : paged([])),
    })
    expect(panel().find('[role="alert"]').text()).toContain('Could not look for an attribution of this comparison')
    expect(panel().find('[role="alert"]').text()).toContain('database is locked')
    fail = false
    await panel().findAll('button').find((b) => b.text() === 'Retry')!.trigger('click')
    await flushPromises()
    expect(panel().find('.callout.start').exists()).toBe(true)
  })

  it('reports a stored ablation that cannot be loaded', async () => {
    const { panel } = await open('', { 'GET /api/ablations': paged([ablationItem()]) })
    expect(panel().find('[role="alert"]').text()).toContain('Could not load the attribution')
  })

  it('summarises the total, the solo effects and the interaction, in words', async () => {
    const { panel } = await open('', stored())
    const metrics = panel().findAll('.summary .metric').map((m) => [m.find('dt').text(), m.find('dd').text()])
    expect(metrics).toEqual([
      ['Total change (B − A)', '+40.0 pp'],
      ['Sum of solo effects', '±0.0 pp'],
      ['Interaction', '+40.0 pp'],
      ['Cases compared', 'n = 10 · A 5/10 → B 9/10'],
    ])
    expect(panel().find('.reading').text()).toBe('Most of the change only appears when dimensions are combined.')
    expect(panel().text()).toContain('simulated')
    expect(panel().text()).not.toContain('effects may be noise')
  })

  it('warns that effects may be noise with a non-deterministic model', async () => {
    const { panel } = await open('', stored(ablation({ deterministic: false })))
    expect(panel().find('.callout.warn').text()).toContain('effects may be noise')
    expect(panel().find('.simulated').exists()).toBe(false)
  })

  it('lists what was not ablated and the server warnings, as text', async () => {
    const hostile = '<img src=x onerror="window.__xss=1">'
    const { panel } = await open(
      '',
      stored(
        ablation({
          not_ablated: [{ path: 'resolved.prompt', dimension: 'prompt', a: 'sha256:aa', b: 'sha256:bb' }],
          warnings: [`Variant judged by another check engine ${hostile}`],
        }),
      ),
    )
    const notes = panel().findAll('.callout.warn')
    expect(notes[0]!.text()).toContain('Not ablated.')
    expect(notes[0]!.find('li').text()).toContain('resolved.prompt (prompt) "sha256:aa" → "sha256:bb"')
    expect(notes[1]!.text()).toBe(`Variant judged by another check engine ${hostile}`)
    expect(panel().find('img').exists()).toBe(false)
  })

  describe('effect of each dimension', () => {
    const row = (panel: DOMWrapper<Element>, dimension: string) => panel.find(`[data-dimension="${dimension}"]`)
    const rect = (r: DOMWrapper<Element>, direction: string) => r.find(`rect[data-direction="${direction}"]`)
    const span = (el: DOMWrapper<Element>) => {
      const x = Number(el.attributes('x'))
      return [x, x + Number(el.attributes('width'))]
    }

    it('draws diverging bars around zero: right when the dimension helps, left when it hurts', async () => {
      const { panel } = await open('', stored())
      const rows = panel().findAll('.rows [data-dimension]')
      expect(rows.map((r) => r.find('button').text())).toEqual(['Prompt', 'Graph', 'Untrusted-output isolation', 'Retry policy'])

      // ±40 pp axis (the +40 pp total sets it): +10 pp spans 50% to 62.5% of the plot.
      const graph = rect(row(panel(), 'graph'), 'forward')
      expect(graph.attributes('data-sign')).toBe('positive')
      expect(span(graph)).toEqual([50, 62.5])
      const retry = rect(row(panel(), 'retry'), 'forward')
      expect(retry.attributes('data-sign')).toBe('negative')
      expect(span(retry)).toEqual([37.5, 50])
      expect(rect(row(panel(), 'retry'), 'backward').attributes('data-sign')).toBe('negative')
      expect(span(rect(row(panel(), 'isolation'), 'backward'))).toEqual([50, 75])
      // No effect alone: no bar, and the value says so.
      expect(rect(row(panel(), 'prompt'), 'forward').exists()).toBe(false)
      expect(row(panel(), 'prompt').find('.line.forward').text()).toBe('alone from A ±0.0 pp 0 gained, 0 lost')
      expect(row(panel(), 'retry').find('.line.backward').text()).toBe('removed from B −10.0 pp 0 gained, 1 lost')
      expect(panel().findAll('.axis .tick').map((t) => [t.text(), t.attributes('style')])).toEqual([
        ['−40 pp', 'left: 0%;'],
        ['−20 pp', 'left: 25%;'],
        ['0', 'left: 50%;'],
        ['+20 pp', 'left: 75%;'],
        ['+40 pp', 'left: 100%;'],
      ])
      expect(row(panel(), 'graph').find('line.total').attributes('x1')).toBe('100')
    })

    it('has a table equivalent, kept in the URL', async () => {
      const { panel, router } = await open('', stored())
      await panel().findAll('[role="group"] button').find((b) => b.text() === 'Table')!.trigger('click')
      await flushPromises()
      expect(router.currentRoute.value.query.effects).toBe('table')
      const table = panel().find('.effects table')
      expect(table.find('caption').text()).toContain('Effect of each dimension on the pass rate')
      const cells = (dimension: string) =>
        table.find(`tr[data-dimension="${dimension}"]`).findAll('td').map((td) => td.text())
      expect(cells('graph')).toEqual(['6/10', '+10.0 pp', '1 / 0', '8/10', '+10.0 pp', '1 / 0', '1', '1'])
      expect(cells('retry')).toEqual(['4/10', '−10.0 pp', '0 / 1', '10/10', '−10.0 pp', '0 / 1', '1', '1'])
      expect(cells('prompt')).toEqual(['5/10', '±0.0 pp', '0 / 0', '7/10', '+20.0 pp', '2 / 0', '0', '2'])
      expect(panel().find('.rows').exists()).toBe(false)

      await panel().findAll('[role="group"] button').find((b) => b.text() === 'Chart')!.trigger('click')
      await flushPromises()
      expect(router.currentRoute.value.query.effects).toBeUndefined()
    })

    it('expands one dimension through the URL, with its values, variants and cases', async () => {
      const { panel, router } = await open('', stored())
      const button = row(panel(), 'prompt').find('button')
      expect(button.attributes('aria-expanded')).toBe('false')
      await button.trigger('click')
      await flushPromises()
      expect(router.currentRoute.value.query.dimension).toBe('prompt')
      expect(button.attributes('aria-expanded')).toBe('true')
      const detail = panel().find(`#${button.attributes('aria-controls')}`)
      expect(detail.find('h4').text()).toBe('Prompt')
      expect(detail.text()).toContain('enough alone for 0 flipped cases, needed in B for 2 flipped cases')
      expect(detail.find('.changes tbody').text()).toBe('prompt_version"p1""p2"')
      const backward = detail.find('[data-direction="backward"]')
      expect(backward.find('h5').text()).toBe('Removed from B · B − prompt')
      expect(hrefs(backward)).toEqual([
        '/evaluations/suite-backward-prompt',
        '/evaluations/suite-backward-prompt/cases/qa-pair',
        '/evaluations/suite-backward-prompt/cases/qa-fixed',
      ])
      expect(backward.text()).toContain('Gained: pass in B, fail without prompt')

      await detail.findAll('button').find((b) => b.text() === 'Collapse')!.trigger('click')
      await flushPromises()
      expect(router.currentRoute.value.query.dimension).toBeUndefined()
      expect(panel().find('.dimension-detail').exists()).toBe(false)
    })

    it('opens a deep-linked dimension in the table view too', async () => {
      const { panel } = await open('&dimension=isolation&effects=table', stored())
      const button = panel().find('tr[data-dimension="isolation"] button')
      expect(button.attributes('aria-expanded')).toBe('true')
      expect(panel().find('.dimension-detail h4').text()).toBe('Untrusted-output isolation')
      expect(panel().find('.dimension-detail .changes tbody').text()).toBe('isolate_untrustedfalsetrue')
    })

    it('says when a variant carried harness errors', async () => {
      const data = ablation()
      data.variants[2] = { ...data.variants[2]!, harness_errors: 2 }
      const { panel } = await open('', stored(data))
      expect(row(panel(), 'graph').find('.line.forward').text()).toContain('2 harness errors')
    })
  })

  describe('interactions', () => {
    it('lists only the pairs that interact, largest first, in words', async () => {
      const { panel } = await open('', stored())
      const section = panel().find('[aria-labelledby="interactions-title"]')
      expect(section.findAll('li .headline').map((h) => h.text())).toEqual([
        'Prompt × Untrusted-output isolation: +30.0 pp, only together',
        'Graph × Retry policy: +10.0 pp, better together than apart',
        'Untrusted-output isolation × Retry policy: −10.0 pp, worse together than apart',
      ])
      expect(section.find('li .detail').text()).toBe(
        'A + prompt + untrusted-output isolation 8/10 (+30.0 pp from A); alone ±0.0 pp and ±0.0 pp',
      )
      expect(section.find('li a').attributes('href')).toBe('/evaluations/suite-pair-prompt-isolation')
      expect(section.text()).toContain('3 other pairs add up to their two solo effects and are not listed.')
    })

    it('says when pairs were skipped', async () => {
      const { panel } = await open('', stored(ablation({ pairs: [], pairs_skipped: true })))
      const section = panel().find('[aria-labelledby="interactions-title"]')
      expect(section.find('[role="note"]').text()).toContain('Pairs were not evaluated')
      expect(section.find('ol').exists()).toBe(false)
    })

    it('says when no pair interacts', async () => {
      const data = ablation()
      data.pairs = data.pairs.map((p) => ({ ...p, interaction_pp: 0 }))
      const { panel } = await open('', stored(data))
      expect(panel().find('[aria-labelledby="interactions-title"]').text()).toContain('No pair interacts')
    })
  })

  describe('flipped cases', () => {
    const caseRow = (panel: DOMWrapper<Element>, caseId: string) => panel.find(`tr[data-case="${caseId}"]`)

    it('lists regressions first, then fixes', async () => {
      const { panel } = await open('', stored())
      const rows = panel().findAll('[aria-labelledby="case-attribution-title"] tbody tr')
      expect(rows.map((r) => [r.find('th').text(), r.find('td').text()])).toEqual([
        ['qa-broken', 'regressed'],
        ['qa-graph', 'fixed'],
        ['qa-pair', 'fixed'],
        ['qa-fixed', 'fixed'],
        ['qa-iso', 'fixed'],
        ['qa-triple', 'fixed'],
      ])
    })

    it('links each chip to its variant run of the case and to a trace diff against A', async () => {
      const { panel } = await open('', stored())
      const broken = caseRow(panel(), 'qa-broken')
      const sufficient = broken.find('td.sufficient .chip')
      expect(sufficient.classes()).toContain('solid')
      expect(sufficient.text()).toBe('Retry policy')
      expect(hrefs(sufficient)).toEqual([
        '/runs/run-forward-retry-qa-broken',
        '/compare/runs?a=run-a-qa-broken&b=run-forward-retry-qa-broken',
      ])
      expect(sufficient.find('a.run').attributes('aria-label')).toBe('Retry policy: the A + retry policy run of qa-broken')
      expect(sufficient.find('a.diff').attributes('aria-label')).toBe('Trace diff of A + retry policy against A on qa-broken')

      const necessary = broken.find('td.necessary .chip')
      expect(necessary.classes()).toContain('outlined')
      expect(hrefs(necessary)).toEqual([
        '/runs/run-backward-retry-qa-broken',
        '/compare/runs?a=run-a-qa-broken&b=run-backward-retry-qa-broken',
      ])
      expect(broken.find('td.combination').text()).toBe('not needed')
    })

    it('shows the pairs a case needs when no single dimension is enough', async () => {
      const { panel } = await open('', stored())
      const pair = caseRow(panel(), 'qa-pair')
      expect(pair.find('td.sufficient').text()).toBe('none')
      expect(pair.findAll('td.necessary .chip').map((c) => c.text())).toEqual(['Prompt', 'Untrusted-output isolation'])
      const combination = pair.find('td.combination')
      expect(combination.find('.tag').text()).toBe('needs combination')
      expect(combination.find('.chip').text()).toBe('Prompt + Untrusted-output isolation')
      expect(hrefs(combination)).toEqual([
        '/runs/run-pair-prompt-isolation-qa-pair',
        '/compare/runs?a=run-a-qa-pair&b=run-pair-prompt-isolation-qa-pair',
      ])
      expect(caseRow(panel(), 'qa-triple').find('td.combination').text()).toContain('no evaluated pair is enough')
    })

    it('keeps a chip without a recorded run as text', async () => {
      const data = ablation()
      const broken = data.cases.find((c) => c.case_id === 'qa-broken')!
      delete broken.runs['forward:retry']
      delete broken.runs.a
      const { panel } = await open('', stored(data))
      const row = caseRow(panel(), 'qa-broken')
      expect(row.find('td.sufficient .chip a').exists()).toBe(false)
      expect(row.find('td.sufficient .chip').text()).toBe('Retry policy')
      // B − retry still opens its run, but without A's run there is no diff to offer.
      expect(hrefs(row.find('td.necessary'))).toEqual(['/runs/run-backward-retry-qa-broken'])
    })
  })

  it('is not shown when no configuration field differs', async () => {
    const { page, fetchMock } = await open('', {
      'GET /api/experiments/exp-1': comparison({
        confounded: false,
        config_diff: [
          { path: 'environment.agentscope_version', dimension: 'other', a: '1.0.0', b: '1.0.1' },
          { path: 'resolved.prompt', dimension: 'prompt', a: 'sha256:aa', b: 'sha256:bb' },
        ],
      }),
    })
    expect(page.find('.config-diff').exists()).toBe(true)
    expect(page.find('#attribution').exists()).toBe(false)
    expect(calls(fetchMock, 'GET', '/api/ablations')).toHaveLength(0)
  })

  it('is forgotten when another comparison is chosen', async () => {
    const { router } = await open('&dimension=prompt&effects=table', stored())
    await wrapper!.findAll('button').find((b) => b.text() === 'Change selection')!.trigger('click')
    await flushPromises()
    expect(router.currentRoute.value.query.dimension).toBeUndefined()
  })
})
