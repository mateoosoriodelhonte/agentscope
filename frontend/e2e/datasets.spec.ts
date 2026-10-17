import { expect, settled, test } from './support/fixtures'

test('dataset import reports problems by JSON pointer, then imports a valid dataset', async ({ page }) => {
  // Versions are immutable, so every run of this spec imports its own dataset name.
  const name = `e2e-import-${Date.now().toString(36)}`
  await page.goto('/datasets')
  await settled(page)
  await page.getByRole('button', { name: 'Import dataset' }).first().click()

  const dialog = page.getByRole('dialog', { name: 'Import a dataset' })
  const source = dialog.getByLabel(/^Dataset JSON/)
  const validate = dialog.getByRole('button', { name: 'Validate' })
  const importButton = dialog.getByRole('button', { name: 'Import', exact: true })

  // 1. Not JSON at all: located at the document root, nothing is sent for validation.
  await source.fill('{"name": "broken",')
  await validate.click()
  const syntax = dialog.getByRole('alert').filter({ hasText: 'Not valid JSON.' })
  await expect(syntax).toBeVisible()
  await expect(syntax.locator('code')).toHaveText('/')
  await expect(importButton).toBeDisabled()

  // 2. JSON, but not a valid dataset: the server's issues, each at its JSON pointer.
  await source.fill(
    JSON.stringify({
      name,
      agent: 'research_assistant',
      cases: [{ id: 'footbridge', task: '', expected: { value: '1987', match: 'roughly' } }],
    }),
  )
  await validate.click()
  const issues = dialog.getByRole('table', { name: 'Validation issues by JSON pointer' })
  await expect(issues).toBeVisible()
  await expect(dialog.getByRole('region', { name: 'Validation result' })).toContainText('2 errors to fix before importing')
  await expect(issues.getByRole('cell', { name: '/cases/0/task', exact: true })).toBeVisible()
  await expect(issues.getByRole('cell', { name: '/cases/0/expected/match', exact: true })).toBeVisible()
  await expect(importButton).toBeDisabled()

  // 3. A valid dataset: validated, then imported, then shown.
  await source.fill(
    JSON.stringify(
      {
        name,
        version: '1',
        description: 'Imported by the end-to-end tests.',
        agent: 'research_assistant',
        cases: [
          {
            id: 'footbridge',
            task: 'In what year did the Meridian Footbridge open?',
            expected: { value: '1987' },
          },
        ],
      },
      null,
      2,
    ),
  )
  await expect(issues).toBeHidden() // editing the text discards the previous validation
  await validate.click()
  await expect(dialog.getByRole('region', { name: 'Validation result' })).toContainText(/Valid: 1 case, sha256:[0-9a-f]{64}/)
  await importButton.click()

  await expect(page).toHaveURL(new RegExp(`/datasets/${name}/1$`))
  await expect(dialog).toBeHidden()
  await settled(page)
  await expect(page.getByRole('heading', { level: 1 })).toContainText(name)
  await expect(page.getByText('footbridge').first()).toBeVisible()

  await page.goto('/datasets')
  await settled(page)
  await expect(page.getByRole('link', { name: `${name}@1` })).toBeVisible()
})
