import { mkdirSync, writeFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import type { Page } from '@playwright/test'
import { optimizePng } from '../scripts/png'
import { demoExperimentId, demoSuiteId, expect, openThemed, test } from './support/fixtures'

/*
 * README screenshots, regenerated with `npm run screenshots` against the freshly seeded demo
 * server: 1440x900 CSS pixels at device scale factor 2, written to docs/images/ and
 * palette-compressed (scripts/png.ts). Not a visual regression test: font rendering differs
 * between machines, so nothing is compared.
 */

const OUT = join(dirname(fileURLToPath(import.meta.url)), '..', '..', 'docs', 'images')

async function capture(page: Page, name: string): Promise<void> {
  await page.evaluate(() => document.fonts.ready)
  // Nothing focused or hovered: the shot shows the page, not the last click.
  await page.mouse.move(0, 0)
  await page.evaluate(() => (document.activeElement as HTMLElement | null)?.blur())
  const png = await page.screenshot({ animations: 'disabled', caret: 'hide', scale: 'device' })
  mkdirSync(OUT, { recursive: true })
  const optimized = optimizePng(png)
  writeFileSync(join(OUT, name), optimized)
  test.info().annotations.push({ type: 'screenshot', description: `${name}: ${optimized.length} bytes` })
}

for (const theme of ['dark', 'light'] as const) {
  test(`overview-${theme}.png`, async ({ page }) => {
    await openThemed(page, '/overview', theme)
    await expect(page.getByRole('region', { name: 'Start here' })).toBeVisible()
    await capture(page, `overview-${theme}.png`)
  })

  test(`trace-${theme}.png`, async ({ page, request }) => {
    const suite = await demoSuiteId(request, 'research_assistant@v1')
    await openThemed(page, `/evaluations/${suite}/cases/injection-corpus-footbridge?check=policy_compliance`, theme)
    await expect(page).toHaveURL(/[?&]span=[0-9a-f]{16}/)
    const tree = page.getByRole('treegrid', { name: 'Steps and spans' })
    await expect(tree.locator('tr[aria-selected="true"].evidence')).toBeInViewport()
    await capture(page, `trace-${theme}.png`)
  })
}

test('evaluation-dark.png', async ({ page, request }) => {
  const suite = await demoSuiteId(request, 'research_assistant@v1')
  await openThemed(page, `/evaluations/${suite}`, 'dark')
  await expect(page.getByRole('grid', { name: 'Cases by check' })).toBeVisible()
  await capture(page, 'evaluation-dark.png')
})

test('compare-dark.png', async ({ page, request }) => {
  const experiment = await demoExperimentId(request, 'Demo: research v1 vs v2')
  await openThemed(page, `/compare?experiment=${experiment}&case=injection-corpus-footbridge`, 'dark')
  const flip = page.getByRole('button', { name: /injection-corpus-footbridge/ })
  await expect(flip).toHaveAttribute('aria-pressed', 'true')
  const diff = page.getByRole('region', { name: 'Trace diff', exact: true })
  await expect(diff.getByRole('status')).toContainText('First divergence:')
  // The diff of the selected flip, from its heading ("Trace diff · <case>") down.
  await page.locator('#trace-diff').evaluate((element) => element.scrollIntoView({ block: 'start' }))
  await expect(diff).toBeInViewport()
  await capture(page, 'compare-dark.png')
})

test('attribution-dark.png', async ({ page, request }) => {
  const experiment = await demoExperimentId(request, 'Demo: research v1 vs v2')
  await openThemed(page, `/compare?experiment=${experiment}`, 'dark')
  const panel = page.getByRole('region', { name: 'Attribution' })
  await expect(panel.getByRole('region', { name: 'Effect of each dimension' })).toBeVisible()
  await expect(panel.getByRole('region', { name: 'Interactions' }).getByRole('listitem').first()).toBeVisible()
  // The stored ablation of the demo comparison, from the panel's heading down.
  await page.locator('#attribution').evaluate((element) => element.scrollIntoView({ block: 'start' }))
  await expect(panel.getByRole('heading', { name: 'Attribution', exact: true })).toBeInViewport()
  await capture(page, 'attribution-dark.png')
})

test('failures-light.png', async ({ page }) => {
  await openThemed(page, '/failures', 'light')
  await expect(page.getByRole('region', { name: 'Prompt injection' })).toBeVisible()
  await capture(page, 'failures-light.png')
})

test('datasets-light.png', async ({ page }) => {
  await openThemed(page, '/datasets', 'light')
  await expect(page.getByRole('link', { name: 'research-assistant@1' })).toBeVisible()
  await capture(page, 'datasets-light.png')
})

test('settings-light.png', async ({ page }) => {
  await openThemed(page, '/settings', 'light')
  await expect(page.getByRole('table', { name: 'Storage inventory' })).toBeVisible()
  await capture(page, 'settings-light.png')
})
