import { describe, expect, it } from 'vitest'

/*
 * Trace and dataset content is hostile. Nothing in the app may turn strings into markup:
 * no v-html, no innerHTML/outerHTML assignment, no insertAdjacentHTML.
 */
const sources = import.meta.glob<string>(['../**/*.vue', '../**/*.ts', '!../**/*.spec.ts'], {
  query: '?raw',
  import: 'default',
  eager: true,
})

const RAW_HTML = /v-html\s*=|\.(?:inner|outer)HTML\b|insertAdjacentHTML|document\.write\(/

describe('raw HTML sinks', () => {
  it('scans the application sources', () => {
    expect(Object.keys(sources).length).toBeGreaterThan(20)
  })

  it.each(Object.entries(sources))('%s renders strings as text only', (_path, source) => {
    expect(source).not.toMatch(RAW_HTML)
  })
})
