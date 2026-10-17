import { expect, settled, test } from './support/fixtures'

test('settings list the egress destinations and the storage inventory', async ({ page }) => {
  await page.goto('/settings')
  await settled(page)
  await expect(page.getByRole('heading', { level: 1, name: 'Settings' })).toBeVisible()

  // Default settings: the local Ollama server is the only destination, on loopback.
  const egress = page.getByRole('region', { name: 'Network destinations' })
  await expect(egress.getByRole('listitem')).toHaveText(['ollama: http://127.0.0.1:11434'])

  const storage = page.getByRole('region', { name: 'Stored data' })
  await expect(storage.getByText('sqlite', { exact: true })).toBeVisible()
  const inventory = storage.getByRole('table', { name: 'Storage inventory' })
  for (const table of ['runs', 'spans', 'datasets', 'suite_runs', 'case_results', 'check_results', 'experiments']) {
    const row = inventory.getByRole('row').filter({ has: page.getByRole('rowheader', { name: table, exact: true }) })
    await expect(row).toHaveCount(1)
    // The demo seed stored rows in every table.
    await expect(row.getByRole('cell').first()).toHaveText(/^[1-9][\d,]*$/)
  }
  await expect(inventory).toContainText('Size on disk:')

  // The purge dialog needs the typed confirmation; this spec only opens and cancels it,
  // because every spec shares this database.
  await storage.getByRole('button', { name: 'Purge data…' }).click()
  const purge = page.getByRole('dialog', { name: 'Purge stored data' })
  await expect(purge.getByRole('button', { name: 'Purge', exact: true })).toBeDisabled()
  await purge.getByRole('button', { name: 'Cancel' }).click()
  await expect(purge).toBeHidden()
  await expect(inventory.getByRole('row').filter({ hasText: 'runs' }).first()).toBeVisible()
})
