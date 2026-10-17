import { expect, settled, test } from './support/fixtures'

test('comparing research v1 with v2 shows the confounded label, the flips and a trace diff', async ({ page }) => {
  await page.goto('/compare')
  await settled(page)
  const recent = page.getByRole('region', { name: 'Recent comparisons' })
  const row = recent.getByRole('row').filter({ has: page.getByRole('link', { name: 'Demo: research v1 vs v2' }) })
  await expect(row.getByText('confounded')).toBeVisible()
  await row.getByRole('link', { name: 'Demo: research v1 vs v2' }).click()

  await expect(page).toHaveURL(/\/compare\?experiment=[0-9a-f-]{36}$/)
  await settled(page)
  await expect(page.getByRole('heading', { level: 1 })).toHaveText('Demo: research v1 vs v2')

  // Several configuration dimensions changed at once, so no single change gets the credit.
  const config = page.getByRole('region', { name: 'Configuration differences' })
  await expect(config.getByRole('note')).toContainText('Confounded: attribution not possible.')

  // v1 -> v2 fixes cases and regresses none; the fixes are listed first.
  const cases = page.getByRole('region', { name: 'Cases' })
  await expect(cases.locator('details.regression')).toHaveCount(0)
  const fixed = cases.locator('details.fix')
  await expect(fixed.locator('summary')).toContainText('Fixed')
  const flips = fixed.getByRole('button')
  expect(await flips.count()).toBeGreaterThan(0)

  const flip = fixed.getByRole('button', { name: /injection-corpus-footbridge/ })
  await flip.click()
  await expect(flip).toHaveAttribute('aria-pressed', 'true')
  await expect(page).toHaveURL(/[?&]case=injection-corpus-footbridge/)

  const diff = page.getByRole('region', { name: 'Trace diff · injection-corpus-footbridge' })
  await expect(diff).toBeVisible()
  const traceDiff = diff.getByRole('region', { name: 'Trace diff', exact: true })
  await expect(traceDiff).toBeVisible()
  await expect(traceDiff.getByRole('status')).toContainText('First divergence:')
  await expect(traceDiff).toContainText('A research_assistant@v1')
  await expect(traceDiff).toContainText('B research_assistant@v2')
})
