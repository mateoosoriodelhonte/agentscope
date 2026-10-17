import { expect, settled, test } from './support/fixtures'

test('a run started from the New run drawer lands on its trace with the injected fault', async ({ page }) => {
  await page.goto('/overview')
  await settled(page)
  await page.getByRole('button', { name: 'New run' }).click()

  const drawer = page.getByRole('dialog', { name: 'New run' })
  await expect(drawer).toBeVisible()
  await drawer.getByLabel('Agent version').selectOption('research_assistant@v2')
  await drawer.getByRole('textbox', { name: 'Task' }).fill('In what year did the Meridian Footbridge open?')

  await drawer.getByRole('button', { name: 'Add tool fault' }).click()
  await drawer.getByRole('combobox', { name: /^Tool/ }).selectOption('search_documents')
  await drawer.getByRole('combobox', { name: /^Kind/ }).selectOption('error')
  await drawer.getByRole('textbox', { name: 'On call' }).fill('1')
  await drawer.getByRole('textbox', { name: 'Times' }).fill('1')

  await drawer.getByLabel('Evaluate the run against an expected answer').check()
  await drawer.getByRole('textbox', { name: 'Value', exact: true }).fill('1987')

  await drawer.getByRole('button', { name: 'Start run' }).click()

  // The job runs in the background; when it succeeds the drawer closes on the new trace.
  await expect(page).toHaveURL(/\/runs\/[0-9a-f-]{36}$/, { timeout: 30_000 })
  await expect(drawer).toBeHidden()
  await settled(page)

  await expect(page.getByText('Injected faults')).toBeVisible()
  await expect(page.getByText(/search_documents: error/)).toBeVisible()
  const tree = page.getByRole('treegrid', { name: 'Steps and spans' })
  await page.getByRole('button', { name: /Expand all/ }).click()
  await expect(tree.getByText('injected error').first()).toBeVisible()

  // v2 retries the failed search and still answers correctly.
  const verdict = page.getByRole('region', { name: 'Verdict' })
  await expect(verdict.getByText('passed', { exact: true }).first()).toBeVisible()
})
