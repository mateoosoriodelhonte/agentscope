import { mkdtempSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { defineConfig, devices } from '@playwright/test'

/*
 * End-to-end tests against the real stack: the built dashboard served by `agentscope serve`
 * with the deterministic demo story seeded into a throwaway database. Nothing here talks to
 * anything but 127.0.0.1:8799.
 *
 *   npm run test:e2e       the specs (chromium)
 *   npm run screenshots    regenerates docs/images/*.png from the same seeded server
 */
export const PORT = 8799
export const BASE_URL = `http://127.0.0.1:${PORT}`

// One fresh data directory per run, created by the runner process; workers inherit the
// variable, so they never create directories of their own.
const dataDir = (process.env.AGENTSCOPE_E2E_DATA_DIR ??= mkdtempSync(join(tmpdir(), 'agentscope-e2e-')))

export default defineConfig({
  testDir: './e2e',
  outputDir: './test-results/e2e',
  timeout: 60_000,
  expect: { timeout: 10_000 },
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  // The specs share one server; it runs at most two agent jobs at once.
  workers: 2,
  reporter: process.env.CI
    ? [['list'], ['html', { open: 'never', outputFolder: 'playwright-report' }]]
    : [['list']],
  use: {
    baseURL: BASE_URL,
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    video: 'off',
    // The dashboard needs at least 1000px (its shell has a minimum width).
    viewport: { width: 1440, height: 900 },
    // Keep the browser from contacting anything on its own.
    serviceWorkers: 'block',
  },
  projects: [
    {
      name: 'chromium',
      testIgnore: /screenshots\.spec\.ts/,
      use: { ...devices['Desktop Chrome'], viewport: { width: 1440, height: 900 } },
    },
    {
      name: 'screenshots',
      testMatch: /screenshots\.spec\.ts/,
      use: { ...devices['Desktop Chrome'], viewport: { width: 1440, height: 900 }, deviceScaleFactor: 2 },
    },
  ],
  webServer: {
    // From the repository root: build the dashboard the server will serve, seed the demo
    // story, then serve API and dashboard on loopback.
    command: [
      'npm --prefix frontend exec -- vite build frontend --logLevel warn',
      'uv run agentscope demo seed',
      `uv run agentscope serve --port ${PORT}`,
    ].join(' && '),
    cwd: '..',
    url: `${BASE_URL}/api/health`,
    // Default settings otherwise: the settings page lists the default local Ollama target.
    env: { AGENTSCOPE_DATA_DIR: dataDir },
    reuseExistingServer: !process.env.CI,
    timeout: 180_000,
    stdout: 'ignore',
    stderr: 'pipe',
  },
})
