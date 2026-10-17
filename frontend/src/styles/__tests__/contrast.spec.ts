import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

/*
 * WCAG AA for the semantic tokens, in both themes: 4.5:1 for every text and status colour
 * on every surface (status labels are text), and 3:1 for non-text glyphs (span-kind
 * gutters, focus ring, duration bars). Read from disk: Vitest does not load stylesheets.
 */
const css = readFileSync(resolve(__dirname, '../tokens.css'), 'utf8')

const SURFACES = ['surface-0', 'surface-1', 'surface-2', 'hover', 'selected']
const TEXT = ['text', 'text-muted', 'text-faint', 'accent', 'ok', 'fail', 'warn', 'na', 'neutral', 'injected', 'redacted']
const GLYPHS = [
  'ok',
  'fail',
  'warn',
  'na',
  'neutral',
  'injected',
  'kind-node',
  'kind-llm',
  'kind-tool',
  'kind-guardrail',
  'focus',
  'bar',
  'series-1',
  'series-2',
  'series-3',
  'series-4',
]
// Badges and chips put status text on the matching soft background.
const PAIRS: [string, string][] = [
  ...['accent', 'ok', 'fail', 'warn', 'na', 'neutral', 'injected', 'redacted'].map(
    (tone): [string, string] => [tone, `${tone}-soft`],
  ),
  ...['ok', 'fail', 'warn', 'na', 'neutral', 'injected', 'redacted'].map(
    (tone): [string, string] => ['text', `${tone}-soft`],
  ),
  ['accent-contrast', 'accent'],
  // Destructive buttons put surface-coloured text on the failure colour.
  ['surface-1', 'fail'],
]
// Duration bars sit on their own track.
const GLYPH_PAIRS: [string, string][] = [['bar', 'bar-track']]

function block(selector: RegExp): Record<string, string> {
  const body = selector.exec(css)?.[1]
  if (!body) throw new Error(`no token block for ${String(selector)}`)
  const tokens: Record<string, string> = {}
  for (const [, name, value] of body.matchAll(/--([\w-]+):\s*(#[0-9a-f]{6})\s*;/gi)) {
    tokens[name!] = value!.toLowerCase()
  }
  return tokens
}

const THEMES = {
  light: block(/:root,\s*:root\[data-theme='light'\]\s*\{([^}]*)\}/),
  dark: block(/:root\[data-theme='dark'\]\s*\{([^}]*)\}/),
}

function luminance(hex: string): number {
  const n = Number.parseInt(hex.slice(1), 16)
  const [r, g, b] = [(n >> 16) & 255, (n >> 8) & 255, n & 255].map((channel) => {
    const c = channel / 255
    return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4
  })
  return 0.2126 * r! + 0.7152 * g! + 0.0722 * b!
}

export function contrast(a: string, b: string): number {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x)
  return (hi! + 0.05) / (lo! + 0.05)
}

describe.each(Object.entries(THEMES))('%s theme tokens', (_theme, tokens) => {
  const color = (name: string) => {
    const value = tokens[name]
    if (!value) throw new Error(`missing token --${name}`)
    return value
  }

  it('define the same colour tokens as the other theme', () => {
    expect(Object.keys(tokens).sort()).toEqual(Object.keys(THEMES.light).sort())
  })

  it.each(TEXT.flatMap((text) => SURFACES.map((surface) => [text, surface])))(
    'text --%s on --%s is at least 4.5:1',
    (text, surface) => {
      expect(contrast(color(text!), color(surface!))).toBeGreaterThanOrEqual(4.5)
    },
  )

  it.each(GLYPHS.flatMap((glyph) => SURFACES.map((surface) => [glyph, surface])))(
    'glyph --%s on --%s is at least 3:1',
    (glyph, surface) => {
      expect(contrast(color(glyph!), color(surface!))).toBeGreaterThanOrEqual(3)
    },
  )

  it.each(PAIRS)('text --%s on --%s is at least 4.5:1', (text, background) => {
    expect(contrast(color(text), color(background))).toBeGreaterThanOrEqual(4.5)
  })

  it.each(GLYPH_PAIRS)('glyph --%s on --%s is at least 3:1', (glyph, background) => {
    expect(contrast(color(glyph), color(background))).toBeGreaterThanOrEqual(3)
  })
})

describe('contrast', () => {
  it('matches the WCAG reference values', () => {
    expect(contrast('#000000', '#ffffff')).toBeCloseTo(21, 5)
    expect(contrast('#777777', '#ffffff')).toBeCloseTo(4.48, 2)
  })
})
