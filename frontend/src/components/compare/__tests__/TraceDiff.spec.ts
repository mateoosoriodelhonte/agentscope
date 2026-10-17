import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { afterEach, describe, expect, it } from 'vitest'
import { createMemoryHistory, createRouter } from 'vue-router'
import { loadFixture, type FixtureName } from '@/__tests__/fixtures'
import { elsewhere } from '@/__tests__/router'
import TraceDiff from '../TraceDiff.vue'

let wrapper: VueWrapper | null = null

async function render(a: FixtureName, b: FixtureName, tweak?: (b: ReturnType<typeof loadFixture>) => void) {
  const router = createRouter({ history: createMemoryHistory(), routes: [elsewhere] })
  await router.push('/')
  const right = loadFixture(b)
  tweak?.(right)
  wrapper = mount(TraceDiff, {
    props: { a: loadFixture(a), b: right, labelA: 'A v1', labelB: 'B v2' },
    global: { plugins: [router] },
    attachTo: document.body,
  })
  await flushPromises()
  return wrapper
}

afterEach(() => {
  wrapper?.unmount()
  wrapper = null
})

describe('TraceDiff', () => {
  it('names the first divergence and shows unmatched steps as gap rows', async () => {
    const diff = await render('run-v1', 'run-v2')
    expect(diff.find('.callout.warn').text()).toContain(
      'First divergence: A v1 step 1 and B v2 step 1 both made a tool_call:search_documents decision, but the tool result differs.',
    )
    const rows = diff.findAll('tbody tr')
    expect(rows).toHaveLength(4)
    expect(rows[1]!.classes()).toContain('only_a')
    expect(rows[1]!.find('td.b').classes()).toContain('gap')
    expect(rows[1]!.find('td.b').text()).toBe('no matching step')
    expect(rows[1]!.find('td.a').text()).toContain('tool_call:send_email')
    expect(rows[1]!.find('.kind-col').text()).toContain('only in A')
    expect(rows[2]!.find('.kind-col').text()).toContain('same')
  })

  it('links each step to its row in the trace explorer', async () => {
    const diff = await render('run-v1', 'run-v2')
    const link = diff.findAll('tbody tr')[1]!.find('td.a a')
    expect(link.attributes('href')).toBe(`/runs/${loadFixture('run-v1').trace.run_id}?span=step-2`)
  })

  it('jumps between divergences with the buttons and with n / Shift N', async () => {
    const diff = await render('run-v1', 'run-v2')
    const current = () => diff.find('tr[aria-current="true"]').attributes('data-row')
    expect(diff.find('.toolbar').text()).toContain('3 divergences in 4 aligned steps')
    expect(current()).toBe('0')
    await diff.findAll('.toolbar button')[1]!.trigger('click')
    expect(current()).toBe('1')
    const region = diff.find('[role="region"]')
    await region.trigger('keydown', { key: 'n' })
    expect(current()).toBe('3')
    await region.trigger('keydown', { key: 'n' })
    expect(current()).toBe('0')
    await region.trigger('keydown', { key: 'N' })
    expect(current()).toBe('3')
  })

  it('puts final statuses, verdicts and outputs side by side', async () => {
    const diff = await render('run-v1-strict', 'run-v2')
    const finals = diff.findAll('.final')
    expect(finals).toHaveLength(2)
    expect(finals[0]!.text()).toContain('failed')
    expect(finals[0]!.text()).toContain('required_tool_calls')
    expect(finals[0]!.find('.json').text()).toContain('AS-CANARY-RA-7Q2X')
    expect(finals[1]!.text()).toContain('passed')
  })

  it('announces a deterministic replay for two simulated runs with the same effective config', async () => {
    const diff = await render('run-v2', 'run-v2', (b) => {
      b.trace.run_id = 'replayed'
    })
    expect(diff.find('.banner').text()).toContain('Identical: deterministic replay')
    expect(diff.findAll('.toolbar button').every((b) => b.attributes('disabled') !== undefined)).toBe(true)
  })

  it('does not call two different configurations a replay', async () => {
    const diff = await render('run-v2', 'run-v3')
    expect(diff.find('.banner').exists()).toBe(false)
  })
})
