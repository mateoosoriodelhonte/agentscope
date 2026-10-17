import AxeBuilder from '@axe-core/playwright'
import type { APIRequestContext, Page } from '@playwright/test'
import {
  caseRunId,
  demoExperimentId,
  demoSuiteId,
  expect,
  openThemed,
  test,
} from './support/fixtures'

/*
 * axe-core on every main screen, in both themes. Serious and critical violations fail the
 * test; the report names each rule and the elements it flagged.
 */

const WCAG = ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa']

interface Screen {
  name: string
  path: (request: APIRequestContext) => Promise<string>
  /** Brings the screen into the state worth scanning (a dialog open, a row selected). */
  prepare?: (page: Page) => Promise<void>
}

const SCREENS: Screen[] = [
  { name: 'overview', path: async () => '/overview' },
  { name: 'runs', path: async () => '/runs' },
  {
    name: 'trace',
    path: async (request) => {
      const suite = await demoSuiteId(request, 'research_assistant@v1')
      const run = await caseRunId(request, suite, 'injection-corpus-footbridge')
      return `/runs/${run}?check=policy_compliance`
    },
  },
  { name: 'evaluations', path: async () => '/evaluations' },
  {
    name: 'evaluation',
    path: async (request) => `/evaluations/${await demoSuiteId(request, 'research_assistant@v1')}`,
  },
  {
    name: 'compare',
    path: async (request) => {
      const experiment = await demoExperimentId(request, 'Demo: research v1 vs v2')
      return `/compare?experiment=${experiment}&case=injection-corpus-footbridge`
    },
    prepare: async (page) => {
      await expect(page.getByRole('region', { name: 'Trace diff', exact: true })).toBeVisible()
    },
  },
  { name: 'failures', path: async () => '/failures' },
  { name: 'agents', path: async () => '/agents' },
  { name: 'agent version', path: async () => '/agents/research_assistant/v2' },
  { name: 'datasets', path: async () => '/datasets' },
  { name: 'dataset', path: async () => '/datasets/research-assistant/1' },
  { name: 'settings', path: async () => '/settings' },
  {
    name: 'new run drawer',
    path: async () => '/overview',
    prepare: async (page) => {
      await page.getByRole('button', { name: 'New run' }).click()
      const drawer = page.getByRole('dialog', { name: 'New run' })
      await drawer.getByRole('button', { name: 'Add tool fault' }).click()
    },
  },
]

for (const theme of ['light', 'dark'] as const) {
  test.describe(`${theme} theme`, () => {
    for (const screen of SCREENS) {
      test(`${screen.name} has no serious accessibility violations`, async ({ page, request }) => {
        await openThemed(page, await screen.path(request), theme)
        await screen.prepare?.(page)
        const results = await new AxeBuilder({ page }).withTags(WCAG).analyze()
        const blocking = results.violations
          .filter((v) => v.impact === 'serious' || v.impact === 'critical')
          .map((v) => ({
            rule: v.id,
            impact: v.impact,
            help: v.help,
            nodes: v.nodes.slice(0, 5).map((n) => `${n.target.join(' ')}: ${n.failureSummary ?? ''}`),
          }))
        expect(blocking, `${screen.name} (${theme})`).toEqual([])
      })
    }
  })
}
