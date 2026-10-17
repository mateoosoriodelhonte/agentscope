import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import type { MetricDelta } from '@/api/types'
import MetricDeltaTable from '../MetricDeltaTable.vue'
import { direction, formatDelta } from '../deltas'

const delta = (overrides: Partial<MetricDelta>): MetricDelta => ({
  metric: 'pass_rate',
  unit: 'pp',
  a: 60,
  b: 80,
  delta: 20,
  higher_is_better: true,
  n: 10,
  ...overrides,
})

const DELTAS = [
  delta({}),
  delta({ metric: 'invalid_outputs', unit: 'count', a: 0, b: 2, delta: 2, higher_is_better: false }),
  delta({ metric: 'unauthorized_tool_attempts', unit: 'count', a: 3, b: 0, delta: -3, higher_is_better: false }),
  delta({ metric: 'harness_errors', unit: 'count', a: 1, b: 1, delta: 0, higher_is_better: false }),
  delta({ metric: 'avg_steps', unit: 'steps', a: 4, b: 3.5, delta: -0.5, higher_is_better: false }),
]

describe('MetricDeltaTable', () => {
  it('colours each delta by whether the change is good for that metric, with a text label', () => {
    const wrapper = mount(MetricDeltaTable, { props: { deltas: DELTAS, deterministic: true } })
    const row = (metric: string) => wrapper.find(`tr[data-metric="${metric}"]`)
    // Higher pass rate: better. More invalid outputs: worse, though the number went up.
    expect(row('pass_rate').classes()).toContain('ok')
    expect(row('pass_rate').text()).toContain('better')
    expect(row('invalid_outputs').classes()).toContain('fail')
    expect(row('invalid_outputs').text()).toContain('worse')
    // Fewer unauthorized attempts: better, though the number went down.
    expect(row('unauthorized_tool_attempts').classes()).toContain('ok')
    expect(row('harness_errors').classes()).toContain('na')
    expect(row('harness_errors').text()).toContain('no change')
    expect(row('avg_steps').classes()).toContain('ok')
  })

  it('shows A, B, the delta in pp or counts, and n; never a relative percentage', () => {
    const wrapper = mount(MetricDeltaTable, { props: { deltas: DELTAS, deterministic: true } })
    const cells = (metric: string) => wrapper.find(`tr[data-metric="${metric}"]`).findAll('td').map((td) => td.text())
    expect(cells('pass_rate').slice(0, 4)).toEqual(['60%', '80%', '+20 pp', '10'])
    expect(cells('invalid_outputs').slice(0, 4)).toEqual(['0', '2', '+2', '10'])
    expect(cells('unauthorized_tool_attempts').slice(0, 3)).toEqual(['3', '0', '−3'])
    expect(cells('avg_steps').slice(0, 3)).toEqual(['4.0', '3.5', '−0.5'])
    expect(wrapper.text()).not.toContain('33%')
  })

  it('labels attempts as simulated susceptibility only for the simulated model', () => {
    let wrapper = mount(MetricDeltaTable, { props: { deltas: DELTAS, deterministic: true } })
    expect(wrapper.find('tr[data-metric="unauthorized_tool_attempts"]').text()).toContain('simulated susceptibility')
    wrapper = mount(MetricDeltaTable, { props: { deltas: DELTAS, deterministic: false } })
    expect(wrapper.find('tr[data-metric="unauthorized_tool_attempts"]').text()).not.toContain('simulated')
  })
})

describe('delta helpers', () => {
  it('treats float noise as no change', () => {
    expect(direction(delta({ delta: 1e-9 })).label).toBe('no change')
    expect(formatDelta(delta({ delta: -12.5 }))).toBe('−12.5 pp')
  })
})
