import type { JsonValue } from '@/api/types'

export type JsonTokenKind =
  | 'key'
  | 'string'
  | 'number'
  | 'boolean'
  | 'null'
  | 'punct'
  | 'redacted'
  | 'truncated'

export interface JsonToken {
  kind: JsonTokenKind
  text: string
}

export interface JsonLine {
  depth: number
  tokens: JsonToken[]
}

// Markers written by src/agentscope/tracing/redaction.py and recorder.py.
const MARKERS = /(\[REDACTED(?::[a-z_]+)?\]|… \[truncated \d+ chars\]|\[TRUNCATED:depth\])/

const punct = (text: string): JsonToken => ({ kind: 'punct', text })

function stringTokens(value: string, kind: 'string' | 'key'): JsonToken[] {
  return JSON.stringify(value)
    .split(MARKERS)
    .filter((part) => part !== '')
    .map((part) => {
      if (part.startsWith('[REDACTED')) return { kind: 'redacted', text: part }
      if (MARKERS.test(part)) return { kind: 'truncated', text: part }
      return { kind, text: part }
    })
}

function scalarTokens(value: string | number | boolean | null): JsonToken[] {
  if (value === null) return [{ kind: 'null', text: 'null' }]
  if (typeof value === 'string') return stringTokens(value, 'string')
  return [{ kind: typeof value === 'number' ? 'number' : 'boolean', text: String(value) }]
}

/**
 * Pretty-prints JSON as lines of typed tokens. JsonView renders every token as a text node,
 * so payload content can never become markup.
 */
export function jsonLines(value: JsonValue): JsonLine[] {
  const lines: JsonLine[] = []
  const walk = (node: JsonValue, depth: number, prefix: JsonToken[], comma: boolean) => {
    const tail = comma ? ',' : ''
    if (Array.isArray(node) || (node !== null && typeof node === 'object')) {
      const entries: [string | null, JsonValue][] = Array.isArray(node)
        ? node.map((item) => [null, item])
        : Object.entries(node)
      const [open, close] = Array.isArray(node) ? ['[', ']'] : ['{', '}']
      if (entries.length === 0) {
        lines.push({ depth, tokens: [...prefix, punct(`${open}${close}${tail}`)] })
        return
      }
      lines.push({ depth, tokens: [...prefix, punct(open)] })
      entries.forEach(([key, child], i) => {
        const keyTokens = key === null ? [] : [...stringTokens(key, 'key'), punct(': ')]
        walk(child, depth + 1, keyTokens, i < entries.length - 1)
      })
      lines.push({ depth, tokens: [punct(`${close}${tail}`)] })
      return
    }
    lines.push({ depth, tokens: [...prefix, ...scalarTokens(node), ...(comma ? [punct(',')] : [])] })
  }
  walk(value, 0, [], false)
  return lines
}

/** Contract models arrive as JSON documents, so any of them can be shown in a JsonView. */
export function asJson(value: object): JsonValue {
  return value as unknown as JsonValue
}
