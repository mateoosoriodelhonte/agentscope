import AxeBuilder from '@axe-core/playwright'
import { demoExperimentId, expect, openThemed, settled, test } from './support/fixtures'

/*
 * The demo seed stores the ablation of "Demo: research v1 vs v2", so its Compare page shows
 * the attribution without running anything. The fake model's documented rule is that the
 * instruction-hierarchy prompt and untrusted-output isolation only work together: neither
 * explains the change alone, the interaction is positive, and their pair locates it.
 */

const WCAG = ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa']
const PROMPT_AND_ISOLATION = /Prompt × Untrusted-output isolation|Untrusted-output isolation × Prompt/

test('the demo comparison credits its change to prompt and isolation together', async ({ page, request }) => {
  const experiment = await demoExperimentId(request, 'Demo: research v1 vs v2')
  await page.goto(`/compare?experiment=${experiment}`)
  await settled(page)

  const panel = page.getByRole('region', { name: 'Attribution' })
  const effects = panel.getByRole('region', { name: 'Effect of each dimension' })
  await expect(effects).toBeVisible()
  // Attributed by the seed: there is nothing to start.
  await expect(panel.getByRole('button', { name: 'Attribute the change' })).toHaveCount(0)
  await expect(panel.locator('.summary .metric').filter({ hasText: 'Interaction' }).locator('dd')).toHaveText(
    /^\+\d+\.\d pp$/,
  )
  await expect(panel.getByText('simulated', { exact: true })).toBeVisible()

  // Both dimensions are ablated, and B needs each of them.
  for (const name of ['Prompt', 'Untrusted-output isolation']) {
    await expect(effects.getByRole('button', { name, exact: true })).toBeVisible()
  }
  await effects.getByRole('button', { name: 'Table' }).click()
  await expect(page).toHaveURL(/[?&]effects=table/)
  const table = effects.getByRole('table', { name: /Effect of each dimension/ })
  for (const dimension of ['prompt', 'isolation']) {
    await expect(table.locator(`tr[data-dimension="${dimension}"] td[data-col="backward-effect"]`)).toHaveText(/^\+\d/)
  }

  // Their pair carries a positive interaction.
  const pair = panel
    .getByRole('region', { name: 'Interactions' })
    .getByRole('listitem')
    .filter({ hasText: PROMPT_AND_ISOLATION })
  await expect(pair).toHaveCount(1)
  await expect(pair).toContainText(/: \+\d+\.\d pp, /)

  // A dimension opens in detail, kept in the URL.
  await table.getByRole('button', { name: 'Prompt', exact: true }).click()
  await expect(page).toHaveURL(/[?&]dimension=prompt/)
  await expect(effects.getByRole('region', { name: 'Prompt' })).toContainText('prompt_version')

  // Every flipped case is attributed; a chip's arrows open its variant's trace diff against A.
  const cases = panel.getByRole('region', { name: 'Flipped cases' })
  await expect(cases.getByRole('row', { name: /injection-corpus-footbridge/ })).toContainText('fixed')
  const diff = cases.getByRole('link', { name: /^Trace diff of .+ against A on / }).first()
  await expect(diff).toHaveAttribute('href', /^\/compare\/runs\?a=[^&]+&b=[^&]+$/)
  await diff.click()
  await expect(page).toHaveURL(/\/compare\/runs\?a=/)
  await settled(page)
  await expect(page.getByRole('region', { name: 'Trace diff', exact: true })).toBeVisible()
})

for (const theme of ['light', 'dark'] as const) {
  test(`the attribution panel has no serious accessibility violations (${theme})`, async ({ page, request }) => {
    const experiment = await demoExperimentId(request, 'Demo: research v1 vs v2')
    await openThemed(page, `/compare?experiment=${experiment}&dimension=isolation`, theme)
    const panel = page.getByRole('region', { name: 'Attribution' })
    const effects = panel.getByRole('region', { name: 'Effect of each dimension' })
    await expect(effects.getByRole('region', { name: 'Untrusted-output isolation' })).toBeVisible()

    // The chart view, then its table equivalent.
    for (const view of ['chart', 'table'] as const) {
      if (view === 'table') {
        await effects.getByRole('button', { name: 'Table' }).click()
        await expect(effects.getByRole('table', { name: /Effect of each dimension/ })).toBeVisible()
      }
      const results = await new AxeBuilder({ page }).include('#attribution').withTags(WCAG).analyze()
      const blocking = results.violations
        .filter((v) => v.impact === 'serious' || v.impact === 'critical')
        .map((v) => ({
          rule: v.id,
          impact: v.impact,
          help: v.help,
          nodes: v.nodes.slice(0, 5).map((n) => `${n.target.join(' ')}: ${n.failureSummary ?? ''}`),
        }))
      expect(blocking, `attribution ${view} (${theme})`).toEqual([])
    }
  })
}
