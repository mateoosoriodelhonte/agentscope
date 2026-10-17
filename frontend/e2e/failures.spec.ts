import { expect, settled, test } from './support/fixtures'

const EXECUTIONS = 'Unauthorized executions (enforced by runtime permissions, must be 0)'

test('the failures page shows prompt injection contained: zero unauthorized executions', async ({ page }) => {
  await page.goto('/failures')
  await settled(page)
  await expect(page.getByRole('heading', { level: 1, name: 'Failures' })).toBeVisible()
  await expect(page.getByText(/Across 5 evaluations\./)).toBeVisible()

  const injection = page.getByRole('region', { name: 'Prompt injection' })
  const cards = injection.getByRole('listitem')
  // Every demo evaluation (research v1-v3, planner v1-v2) ran prompt-injection cases.
  await expect(cards).toHaveCount(5)
  for (const card of await cards.all()) {
    const executions = card.locator('div').filter({ has: page.getByText(EXECUTIONS, { exact: true }) })
    await expect(executions.locator('dd .mono')).toHaveText('0')
    await expect(executions.getByText('contained', { exact: true })).toBeVisible()
  }
  // v1 still tried: attempts are reported apart, as simulated susceptibility.
  const v1 = cards.filter({ hasText: 'research_assistant@v1' })
  await expect(v1.getByText('Unauthorized attempts (simulated susceptibility)')).toBeVisible()

  await expect(page.getByRole('region', { name: 'Resilience by fault kind' }).getByRole('table')).toBeVisible()
  await expect(page.getByRole('region', { name: 'Failure buckets' })).toBeVisible()
})
