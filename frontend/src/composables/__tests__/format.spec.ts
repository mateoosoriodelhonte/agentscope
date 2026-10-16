import { describe, expect, it } from 'vitest'
import { formatDuration } from '../format'

describe('formatDuration', () => {
  it.each([
    [0.029, '0.03 ms'],
    [0.996, '1.0 ms'],
    [12.34, '12.3 ms'],
    [99.96, '100 ms'],
    [412.4, '412 ms'],
    [999.6, '1.00 s'],
    [2003.187, '2.00 s'],
    [59_999, '1m 00s'],
    [119_700, '2m 00s'],
    [61_000, '1m 01s'],
  ])('%s ms → %s', (ms, text) => {
    expect(formatDuration(ms)).toBe(text)
  })
})
