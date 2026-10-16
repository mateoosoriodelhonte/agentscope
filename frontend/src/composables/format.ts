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
