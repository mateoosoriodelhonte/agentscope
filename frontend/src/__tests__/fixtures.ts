import type { RunDetail } from '@/api/types'

/*
 * Real RunDetail payloads recorded from the deterministic fake provider. Loaded as raw text
 * so the large JSON is not type-inferred; each test gets a fresh copy.
 */
const files = import.meta.glob<string>('../__fixtures__/*.json', {
  query: '?raw',
  import: 'default',
  eager: true,
})

export type FixtureName =
  | 'run-v1'
  | 'run-v1-strict'
  | 'run-v2'
  | 'run-v2-schema-repair'
  | 'run-v2-timeout'
  | 'run-v3'

export function loadFixture(name: FixtureName): RunDetail {
  const text = files[`../__fixtures__/${name}.json`]
  if (text === undefined) throw new Error(`missing fixture ${name}`)
  return JSON.parse(text) as RunDetail
}
