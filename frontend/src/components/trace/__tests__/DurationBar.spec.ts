import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import DurationBar, { barGeometry, type BarInput } from '../DurationBar.vue'

const base: BarInput = {
  mode: 'relative',
  startMs: 40,
  durationMs: 25,
  totalMs: 200,
  scaleMs: 100,
  tickMs: null,
  timelineTick: false,
}

describe('barGeometry', () => {
  it('relative mode starts every bar at zero, scaled to the longest row', () => {
    expect(barGeometry(base)).toEqual({ left: 0, width: 25, tick: null })
    expect(barGeometry({ ...base, durationMs: 150 }).width).toBe(100)
  })

  it('relative mode draws the tool-timeout tick only when it falls inside the scale', () => {
    expect(barGeometry({ ...base, tickMs: 80 }).tick).toBe(80)
    expect(barGeometry({ ...base, tickMs: 2000 }).tick).toBeNull()
  })

  it('timeline mode places bars at their offset on the run time axis', () => {
    const timeline = { ...base, mode: 'timeline' as const }
    expect(barGeometry(timeline)).toEqual({ left: 20, width: 12.5, tick: null })
    expect(barGeometry({ ...timeline, startMs: 190, durationMs: 50 }).width).toBe(5)
  })

  it('timeline mode marks the deadline of a tool attempt at start + timeout', () => {
    const timeline = { ...base, mode: 'timeline' as const, tickMs: 60, timelineTick: true }
    expect(barGeometry(timeline).tick).toBe(50)
    expect(barGeometry({ ...timeline, tickMs: 500 }).tick).toBeNull()
    expect(barGeometry({ ...timeline, timelineTick: false }).tick).toBeNull()
  })
})

describe('DurationBar', () => {
  it('renders the geometry, hatches injected faults and stays out of the accessibility tree', () => {
    const bar = mount(DurationBar, { props: { ...base, tickMs: 50, fault: true } })
    expect(bar.find('.track').attributes('aria-hidden')).toBe('true')
    const fill = bar.find('.fill')
    expect(fill.classes()).toContain('hatched')
    expect((fill.element as HTMLElement).style.width).toBe('25%')
    expect((fill.element as HTMLElement).style.left).toBe('0%')
    expect((bar.find('.tick').element as HTMLElement).style.left).toBe('50%')
  })

  it('switches modes without a tick when none applies', async () => {
    const bar = mount(DurationBar, { props: base })
    expect(bar.find('.tick').exists()).toBe(false)
    expect(bar.find('.fill').classes()).not.toContain('hatched')
    await bar.setProps({ mode: 'timeline' })
    expect((bar.find('.fill').element as HTMLElement).style.left).toBe('20%')
  })
})
