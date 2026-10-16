import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import { loadFixture, type FixtureName } from '@/__tests__/fixtures'
import { useTraceModel } from '@/composables/useTraceModel'
import TransitionStrip from '../TransitionStrip.vue'

function mountStrip(name: FixtureName, current: string | null = null) {
  const { groups } = useTraceModel(loadFixture(name).trace)
  return mount(TransitionStrip, { props: { groups, current } })
}

const chips = (name: FixtureName) =>
  mountStrip(name)
    .findAll('.item')
    .map((item) => [item.find('.segment').text(), item.findAll('.chip').map((c) => c.text())])

describe('TransitionStrip', () => {
  it('compresses normal steps and keeps reason chips for off-loop transitions only', () => {
    expect(chips('run-v2')).toEqual([
      ['S1', []],
      ['S2', []],
      ['S3', []],
      ['End', []],
    ])
    expect(chips('run-v1')).toEqual([
      ['S1', []],
      ['S2', ['denied send_email']],
      ['S3', []],
      ['S4', []],
      ['End', []],
    ])
    expect(chips('run-v2-timeout')[0]).toEqual(['S1', ['retry ×1']])
    expect(chips('run-v2-schema-repair')[0]).toEqual(['S1', ['repair 1/1']])
  })

  it('labels segments for assistive technology and flags faults and failures', () => {
    const strip = mountStrip('run-v2-schema-repair', 'step-2')
    const [s1, s2] = strip.findAll('.segment')
    expect(s1!.attributes('aria-label')).toBe(
      'Step 1: Final answer rejected: schema invalid → repair 1/1 · injected schema_violation',
    )
    expect(s1!.classes()).toEqual(expect.arrayContaining(['fail', 'fault']))
    expect(s2!.attributes('aria-current')).toBe('step')
    expect(s1!.attributes('aria-current')).toBeUndefined()
  })

  it('works as a minimap: activating a segment selects its step', async () => {
    const strip = mountStrip('run-v1')
    await strip.findAll('.segment')[2]!.trigger('click')
    await strip.findAll('.segment')[4]!.trigger('click')
    expect(strip.emitted('select')).toEqual([['step-3'], ['finish']])
  })
})
