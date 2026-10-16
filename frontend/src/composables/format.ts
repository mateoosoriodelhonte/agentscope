import type { JsonValue } from '@/api/types'

/** Durations keep sub-millisecond precision: fake-provider spans are often under 1 ms. */
export function formatDuration(ms: number): string {
  if (ms < 1) return `${ms.toFixed(2)} ms`
  if (ms < 100) return `${ms.toFixed(1)} ms`
  if (ms < 1000) return `${Math.round(ms)} ms`
  if (ms < 60_000) return `${(ms / 1000).toFixed(2)} s`
  const minutes = Math.floor(ms / 60_000)
  const seconds = Math.round((ms % 60_000) / 1000)
  return `${minutes}m ${String(seconds).padStart(2, '0')}s`
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
