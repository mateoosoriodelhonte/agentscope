import { expect, settled, test } from './support/fixtures'

test('a failed check opens the trace on its evidence span', async ({ page }) => {
  await page.goto('/evaluations')
  await settled(page)
  await page.getByRole('link', { name: 'demo: research_assistant@v1' }).click()

  await expect(page).toHaveURL(/\/evaluations\/[0-9a-f-]{36}$/)
  await settled(page)
  await expect(page.getByRole('heading', { level: 1 })).toHaveText('demo: research_assistant@v1')
  // v1 fails the demo gate, while the runtime still executes no unauthorized tool.
  await expect(page.getByRole('region', { name: /gate/i }).getByText('gate failed')).toBeVisible()

  const matrix = page.getByRole('grid', { name: 'Cases by check' })
  const cell = matrix.getByRole('link', { name: 'injection-corpus-footbridge, policy_compliance: failed' })
  await expect(cell).toHaveAttribute('data-status', 'failed')
  await cell.click()

  await expect(page).toHaveURL(/\/evaluations\/[0-9a-f-]{36}\/cases\/injection-corpus-footbridge\?check=policy_compliance/)
  // The deep link selects the check's first evidence span.
  await expect(page).toHaveURL(/[?&]span=[0-9a-f]{16}/)
  const span = new URL(page.url()).searchParams.get('span')!
  await expect(page.getByRole('navigation', { name: 'Breadcrumb' })).toContainText('injection-corpus-footbridge')

  const verdict = page.getByRole('region', { name: 'Verdict' })
  await expect(verdict.getByText('failed', { exact: true }).first()).toBeVisible()
  const check = verdict.getByRole('button', { name: /policy_compliance/ })
  await expect(check).toHaveAttribute('aria-expanded', 'true')
  await expect(verdict.locator('#check-policy_compliance')).toContainText('Evidence span 1 of 1')

  const tree = page.getByRole('treegrid', { name: 'Steps and spans' })
  const selected = tree.locator('tr[aria-selected="true"]')
  await expect(selected).toHaveCount(1)
  await expect(selected).toHaveAttribute('id', new RegExp(`${span}$`))
  await expect(selected).toHaveClass(/\bevidence\b/)
  await expect(selected).toBeInViewport()
  await expect(selected).toContainText('cited ×')
})
