import type { JsonValue } from '@/api/types'

/** Durations keep sub-millisecond precision: fake-provider spans are often under 1 ms. */
export function formatDuration(ms: number): string {
  // Each branch is chosen on the value as it will be rounded, so 999.6 ms reads "1.00 s"
  // rather than "1000 ms", and 119.7 s reads "2m 00s" rather than "1m 60s".
  if (ms < 0.995) return `${ms.toFixed(2)} ms`
  if (ms < 99.95) return `${ms.toFixed(1)} ms`
  if (ms < 999.5) return `${Math.round(ms)} ms`
  if (ms < 59_995) return `${(ms / 1000).toFixed(2)} s`
  const totalSeconds = Math.round(ms / 1000)
  const minutes = Math.floor(totalSeconds / 60)
  return `${minutes}m ${String(totalSeconds % 60).padStart(2, '0')}s`
}

export function formatTimestamp(iso: string): string {
  const date = new Date(iso)
  return Number.isNaN(date.getTime()) ? iso : date.toLocaleString()
}

export function shortId(id: string, length = 8): string {
  return id.length > length ? id.slice(0, length) : id
}

export function plural(count: number, singular: string, pluralForm = `${singular}s`): string {
  return `${count} ${count === 1 ? singular : pluralForm}`
}

export function truncate(text: string, max: number): string {
  return text.length > max ? `${text.slice(0, max - 1)}…` : text
}

/** One-line rendering of a JSON value for summaries; the full value is shown elsewhere. */
export function compact(value: JsonValue | undefined, max = 60): string {
  if (value === undefined || value === null) return '—'
  if (typeof value === 'string') return truncate(JSON.stringify(value), max)
  if (typeof value !== 'object') return String(value)
  return truncate(JSON.stringify(value), max)
}

/** A count over its denominator, e.g. "7/10": rates are shown as k/n, never as relative %. */
export function ratio(k: number, n: number): string {
  return `${k}/${n}`
}

const MINUS = '−'

function signedNumber(value: number, digits: number): string {
  const rounded = Number(value.toFixed(digits))
  if (rounded === 0) return `±${(0).toFixed(digits)}`
  const text = Math.abs(rounded).toFixed(digits)
  return rounded > 0 ? `+${text}` : `${MINUS}${text}`
}

/** Up to one decimal, without a trailing ".0". */
function digitsFor(value: number): number {
  return Number.isInteger(Number(value.toFixed(1))) ? 0 : 1
}

/** A signed difference in percentage points, e.g. "+20 pp" or "−5.5 pp". */
export function formatPp(delta: number): string {
  return `${signedNumber(delta, digitsFor(delta))} pp`
}

/** A signed difference, e.g. "+2", "−1" or "±0". */
export function formatSigned(delta: number, digits = 0): string {
  return signedNumber(delta, digits)
}

/** An absolute percentage of a 0–1 rate, e.g. 0.8 → "80%". */
export function formatPercent(rate: number): string {
  const value = rate * 100
  return `${value.toFixed(digitsFor(value))}%`
}

export function formatBytes(bytes: number | null): string | null {
  if (bytes === null) return null
  if (bytes < 1024) return `${bytes} B`
  const units = ['KiB', 'MiB', 'GiB', 'TiB']
  let value = bytes / 1024
  let unit = 0
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024
    unit += 1
  }
  return `${value.toFixed(value < 10 ? 1 : 0)} ${units[unit]}`
}

/** JSON with sorted keys, so equal values always serialise (and compare) the same. */
export function canonicalJson(value: unknown): string {
  return JSON.stringify(value, (_key, item: unknown) => {
    if (item === null || typeof item !== 'object' || Array.isArray(item)) return item
    return Object.fromEntries(
      Object.entries(item as Record<string, unknown>).sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0)),
    )
  })
}
