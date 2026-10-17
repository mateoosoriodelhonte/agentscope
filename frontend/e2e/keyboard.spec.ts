import type { Page } from '@playwright/test'
import { expect, settled, test } from './support/fixtures'

/*
 * Overview -> failed case -> evidence span with the keyboard alone: every step below is a
 * key press. The test only reads the page to decide when to stop pressing.
 */

type Probe = (element: Element) => boolean

async function focused(page: Page, probe: Probe): Promise<boolean> {
  const handle = await page.evaluateHandle(() => document.activeElement ?? document.body)
  try {
    return await handle.evaluate(probe)
  } finally {
    await handle.dispose()
  }
}

/** Presses `key` until the focused element satisfies `probe` (at most `limit` presses). */
async function pressUntil(page: Page, key: string, probe: Probe, limit = 60): Promise<void> {
  for (let i = 0; i < limit; i++) {
    if (await focused(page, probe)) return
    await page.keyboard.press(key)
  }
  expect(await focused(page, probe), `focus never reached the target pressing ${key}`).toBe(true)
}

test('keyboard only: from the overview to the evidence span of a failed check', async ({ page }) => {
  await page.goto('/overview')
  await settled(page)

  // The first Tab reaches the skip link, which jumps over the sidebar to the content.
  await page.keyboard.press('Tab')
  await expect(page.getByRole('link', { name: 'Skip to content' })).toBeFocused()
  await page.keyboard.press('Enter')

  await pressUntil(page, 'Tab', (el) => el.textContent?.trim() === 'demo: research_assistant@v1')
  await page.keyboard.press('Enter')
  await expect(page).toHaveURL(/\/evaluations\/[0-9a-f-]{36}$/)
  await settled(page)
  // Route changes move focus to the page heading.
  await expect(page.getByRole('heading', { level: 1 })).toBeFocused()

  // The cases x checks grid is a single tab stop; arrows move inside it.
  await pressUntil(page, 'Tab', (el) => el.closest('[role="grid"]') !== null)
  await pressUntil(page, 'ArrowDown', (el) => (el.textContent ?? '').includes('injection-corpus-footbridge'))
  await pressUntil(page, 'ArrowRight', (el) => (el.getAttribute('aria-label') ?? '').includes('policy_compliance'))
  await expect(page.locator(':focus')).toHaveAttribute('data-status', 'failed')
  await page.keyboard.press('Enter')

  await expect(page).toHaveURL(/\/cases\/injection-corpus-footbridge\?check=policy_compliance&span=[0-9a-f]{16}/)
  const span = new URL(page.url()).searchParams.get('span')!
  const tree = page.getByRole('treegrid', { name: 'Steps and spans' })
  const selected = tree.locator('tr[aria-selected="true"]')
  await expect(selected).toHaveAttribute('id', `trace-row-${span}`)
  await expect(selected).toHaveClass(/\bevidence\b/)

  // Into the tree (its selected row is the tab stop), then `n` cycles the check's evidence.
  await pressUntil(page, 'Tab', (el) => el.closest('[role="treegrid"]') !== null)
  await expect(page.locator(':focus')).toHaveAttribute('id', `trace-row-${span}`)
  await page.keyboard.press('j')
  await expect(selected).not.toHaveAttribute('id', `trace-row-${span}`)
  await page.keyboard.press('n')
  await expect(selected).toHaveAttribute('id', `trace-row-${span}`)
  await expect(page.getByRole('region', { name: 'Verdict' }).locator('#check-policy_compliance')).toContainText(
    'Evidence span 1 of 1',
  )
})
