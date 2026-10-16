import { mount } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'
import Metric from '../Metric.vue'
import Skeleton from '../Skeleton.vue'
import SplitPane from '../SplitPane.vue'
import StatusBadge from '../StatusBadge.vue'
import Tabs from '../Tabs.vue'

afterEach(() => {
  vi.useRealTimers()
  vi.restoreAllMocks()
  window.localStorage.clear()
})

describe('StatusBadge', () => {
  it('pairs every status with an icon and a text label', () => {
    for (const status of ['completed', 'failed', 'not_applicable', 'running']) {
      const badge = mount(StatusBadge, { props: { status } })
      expect(badge.find('svg').exists()).toBe(true)
      expect(badge.text()).toBe(status.replace('_', ' '))
    }
    const tones = ['completed', 'failed'].map((status) => mount(StatusBadge, { props: { status } }).classes())
    expect(tones[0]).toContain('ok')
    expect(tones[1]).toContain('fail')
  })
})

describe('Metric', () => {
  it('says "not reported" or "not configured" instead of inventing a value', () => {
    const tokens = mount(Metric, { props: { label: 'Tokens', value: null } })
    expect(tokens.find('dd').text()).toBe('not reported')
    const cost = mount(Metric, { props: { label: 'Cost', value: null, missing: 'not configured' } })
    expect(cost.find('dd').text()).toBe('not configured')
    const steps = mount(Metric, { props: { label: 'Steps', value: '4 / 8' } })
    expect(steps.find('dd').text()).toBe('4 / 8')
  })
})

describe('Skeleton', () => {
  it('stays invisible for the first 200 ms', async () => {
    vi.useFakeTimers()
    const skeleton = mount(Skeleton, { props: { rows: 3 } })
    expect(skeleton.findAll('.bar')).toHaveLength(0)
    await vi.advanceTimersByTimeAsync(199)
    expect(skeleton.findAll('.bar')).toHaveLength(0)
    await vi.advanceTimersByTimeAsync(1)
    expect(skeleton.findAll('.bar')).toHaveLength(3)
    expect(skeleton.attributes('aria-hidden')).toBe('true')
  })
})

describe('Tabs', () => {
  it('selects tabs with arrows, Home and End, with one tab stop', async () => {
    const tabs = mount(Tabs, {
      props: {
        label: 'Detail',
        tabs: [
          { id: 'a', label: 'A' },
          { id: 'b', label: 'B' },
          { id: 'c', label: 'C' },
        ],
        modelValue: 'a',
        'onUpdate:modelValue': (id: string) => tabs.setProps({ modelValue: id }),
      },
      slots: { default: '<p>panel</p>' },
      attachTo: document.body,
    })
    const selected = () => tabs.find('[aria-selected="true"]').text()
    await tabs.find('[role="tab"]').trigger('keydown', { key: 'ArrowLeft' })
    expect(selected()).toBe('C')
    await tabs.find('[aria-selected="true"]').trigger('keydown', { key: 'Home' })
    expect(selected()).toBe('A')
    await tabs.find('[aria-selected="true"]').trigger('keydown', { key: 'ArrowRight' })
    expect(selected()).toBe('B')
    expect(tabs.findAll('[tabindex="0"][role="tab"]')).toHaveLength(1)
    const panel = tabs.find('[role="tabpanel"]')
    expect(panel.attributes('aria-labelledby')).toBe(tabs.find('[aria-selected="true"]').attributes('id'))
    tabs.unmount()
  })
})

describe('SplitPane', () => {
  it('resizes from the keyboard within bounds and remembers the ratio', async () => {
    const split = mount(SplitPane, {
      props: { label: 'Resize panes', storageKey: 'test.split', initial: 0.6, min: 0.3, max: 0.75 },
    })
    const separator = split.find('[role="separator"]')
    expect(separator.attributes()).toMatchObject({
      tabindex: '0',
      'aria-valuenow': '60',
      'aria-valuemin': '30',
      'aria-valuemax': '75',
      'aria-label': 'Resize panes',
    })
    await separator.trigger('keydown', { key: 'ArrowLeft' })
    expect(separator.attributes('aria-valuenow')).toBe('58')
    await separator.trigger('keydown', { key: 'End' })
    expect(separator.attributes('aria-valuenow')).toBe('75')
    await separator.trigger('keydown', { key: 'ArrowRight' })
    expect(separator.attributes('aria-valuenow')).toBe('75')
    expect(window.localStorage.getItem('test.split')).toBe('0.750')
    const again = mount(SplitPane, { props: { label: 'Resize panes', storageKey: 'test.split' } })
    expect(again.find('[role="separator"]').attributes('aria-valuenow')).toBe('75')
  })

  it('works when storage is blocked', () => {
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new Error('blocked')
    })
    const split = mount(SplitPane, { props: { label: 'Resize panes', storageKey: 'test.split' } })
    expect(split.find('[role="separator"]').attributes('aria-valuenow')).toBe('60')
  })
})

describe('useTheme', () => {
  it('persists a manual choice, follows the system otherwise, and survives blocked storage', async () => {
    vi.resetModules()
    const { useTheme } = await import('@/composables/useTheme')
    const theme = useTheme()
    expect(theme.preference.value).toBe('system')
    theme.setPreference('dark')
    await Promise.resolve()
    expect(window.localStorage.getItem('agentscope.theme')).toBe('dark')
    expect(document.documentElement.dataset.theme).toBe('dark')
    theme.setPreference('system')
    expect(window.localStorage.getItem('agentscope.theme')).toBeNull()
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('blocked')
    })
    expect(() => theme.setPreference('light')).not.toThrow()
    expect(theme.theme.value).toBe('light')
  })
})
