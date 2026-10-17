import { expect, settled, test } from './support/fixtures'

test('the overview tells the demo story', async ({ page }) => {
  await page.goto('/')
  await expect(page).toHaveURL(/\/overview$/)
  await settled(page)
  await expect(page.getByRole('heading', { level: 1, name: 'Overview' })).toBeVisible()
  await expect(page).toHaveTitle('Overview · AgentScope')

  // The walkthrough starts from a failing gate and offers the comparison with a passing version.
  const start = page.getByRole('region', { name: 'Start here' })
  await expect(start.getByRole('link', { name: /^Open task_planner@v1 on task-planner@1/ })).toBeVisible()
  await expect(start.getByRole('link', { name: 'Compare with task_planner@v2' })).toBeVisible()

  // Every demo evaluation is listed with its gate verdict: v1 fails, v2 and v3 pass.
  const evaluations = page.getByRole('table', { name: 'Recent evaluations' })
  for (const [agentRef, gate] of [
    ['research_assistant@v1', 'failed'],
    ['research_assistant@v2', 'passed'],
    ['research_assistant@v3', 'passed'],
    ['task_planner@v1', 'failed'],
    ['task_planner@v2', 'passed'],
  ] as const) {
    const row = evaluations.getByRole('row').filter({ has: page.getByRole('link', { name: `demo: ${agentRef}` }) })
    await expect(row).toHaveCount(1)
    await expect(row).toContainText(new RegExp(`gate ${gate}`, 'i'))
  }

  // The ad-hoc demo runs (other specs may add runs of their own), including the compromised
  // model whose injected actions the runtime guard blocked.
  const runs = page.getByRole('table', { name: 'Recent runs' })
  const compromised = runs
    .getByRole('row')
    .filter({ has: page.getByRole('cell', { name: 'research_assistant@v2-scripted-compliant' }) })
  await expect(compromised).toHaveCount(1)
  await expect(compromised.getByRole('link')).toHaveText('When was Aldermoor Water Works commissioned?')
  await expect(runs.getByRole('cell', { name: 'task_planner@v2' }).first()).toBeVisible()

  await expect(page.getByRole('heading', { name: 'Pass rate by agent version' })).toBeVisible()
  await expect(page.getByText('Simulated model').first()).toBeVisible()
})
