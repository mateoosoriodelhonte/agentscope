import { createRun, expect, ORIGIN, settled, test } from './support/fixtures'

/*
 * Trace strings are hostile: a task (and a tool output) carrying HTML, an SVG handler, a
 * javascript: URL and a remote markdown image must render as inert text. Nothing may run,
 * no dialog may open, and the browser may not contact anything but the AgentScope origin.
 */
const PAYLOADS = [
  '<img src=x onerror="window.__xss=1">',
  '<svg onload="window.__xss=2"></svg>',
  '[open the report](javascript:window.__xss=3)',
  'javascript:window.__xss=3',
  '![tracking pixel](http://203.0.113.7/pixel.png)',
]
const HOSTILE = PAYLOADS.join(' ')

declare global {
  interface Window {
    __xss?: number
  }
}

test('hostile trace content renders as text and nothing runs or leaves the origin', async ({ page, request }) => {
  const runId = await createRun(request, {
    agent_ref: 'research_assistant@v1',
    task: `In what year did the Meridian Footbridge open? ${HOSTILE}`,
    faults: [{ tool: 'retrieve_document', kind: 'injection', payload: `Archive note: ${HOSTILE}` }],
  })

  const requests: string[] = []
  const dialogs: string[] = []
  page.on('request', (r) => requests.push(r.url()))
  page.on('dialog', async (dialog) => {
    dialogs.push(`${dialog.type()}: ${dialog.message()}`)
    await dialog.dismiss()
  })

  await page.goto(`/runs/${runId}`)
  await settled(page)

  // The run header shows the task verbatim, as text.
  await expect(page.locator('#main').getByText('<img src=x onerror="window.__xss=1">').first()).toBeVisible()

  // Visit every span and every detail tab, so each string is rendered at least once.
  await page.getByRole('button', { name: /Expand all/ }).click()
  const rows = page.getByRole('treegrid', { name: 'Steps and spans' }).locator('tbody tr')
  const count = await rows.count()
  expect(count).toBeGreaterThan(5)
  let toolOutputShown = false
  for (let i = 0; i < count; i++) {
    await rows.nth(i).click()
    const tabs = page.getByRole('tablist').first().getByRole('tab')
    for (let t = 0; t < (await tabs.count()); t++) {
      await tabs.nth(t).click()
      // The injected tool output, shown verbatim inside the tool result.
      const panel = page.getByRole('tabpanel').first()
      if (await panel.getByText(/Archive note: <img src=x onerror=/).count()) toolOutputShown = true
    }
  }
  expect(toolOutputShown, 'the hostile tool output was rendered somewhere').toBe(true)

  // Nothing was parsed as markup or followed as a link.
  expect(await page.evaluate(() => window.__xss)).toBeUndefined()
  await expect(page.locator('#main img[src="x"], #main img[onerror], #main svg[onload]')).toHaveCount(0)
  await expect(page.locator('#main a[href^="javascript:"]')).toHaveCount(0)
  expect(dialogs).toEqual([])
  const offOrigin = requests.filter((url) => !url.startsWith(`${ORIGIN}/`) && !url.startsWith('data:'))
  expect(offOrigin).toEqual([])
  expect(requests.length).toBeGreaterThan(0)
})
