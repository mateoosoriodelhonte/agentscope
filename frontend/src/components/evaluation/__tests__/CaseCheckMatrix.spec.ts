import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { afterEach, describe, expect, it } from 'vitest'
import { createMemoryHistory, createRouter } from 'vue-router'
import { caseResult } from '@/__tests__/builders'
import { elsewhere } from '@/__tests__/router'
import { CHECK_NAMES } from '@/api/types'
import { ICONS } from '@/components/ui/icons'
import CaseCheckMatrix from '../CaseCheckMatrix.vue'

let wrapper: VueWrapper | null = null

async function render() {
  const router = createRouter({ history: createMemoryHistory(), routes: [elsewhere] })
  await router.push('/')
  const cases = [
    caseResult('qa-1'),
    caseResult('case/2', ['answer_correctness', 'policy_compliance']),
    // policy_compliance did not run for this case (disabled with --check).
    { ...caseResult('qa-3'), checks: caseResult('qa-3').checks.filter((c) => c.name !== 'policy_compliance') },
  ]
  wrapper = mount(CaseCheckMatrix, {
    props: {
      suiteId: 'suite 1',
      cases,
      stats: { answer_correctness: { passed: 2, failed: 1, not_applicable: 0 }, timeout_behavior: { passed: 0, failed: 0, not_applicable: 3 } },
    },
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

const pathsOf = (cell: VueWrapper | ReturnType<VueWrapper['find']>) => cell.findAll('path').map((p) => p.attributes('d'))

describe('CaseCheckMatrix', () => {
  it('has one row per case and one column per check', async () => {
    const matrix = await render()
    expect(matrix.findAll('thead th abbr').map((a) => a.attributes('title'))).toEqual([...CHECK_NAMES])
    expect(matrix.findAll('tbody tr')).toHaveLength(3)
  })

  it('draws each status with a different glyph shape, not only a different colour', async () => {
    const matrix = await render()
    const row = matrix.findAll('tbody tr')[1]!
    const passed = row.find('[data-status="passed"]')
    const failed = row.find('[data-status="failed"]')
    const na = row.find('[data-status="not_applicable"]')
    expect(pathsOf(passed)).toEqual([...ICONS.check])
    expect(pathsOf(failed)).toEqual([...ICONS.x])
    expect(pathsOf(na)).toEqual([...ICONS.minus])
    expect(new Set([pathsOf(passed)[0], pathsOf(failed)[0], pathsOf(na)[0]]).size).toBe(3)
    expect(failed.attributes('aria-label')).toBe('case/2, answer_correctness: failed')
  })

  it('links every cell to the case trace with ?check=', async () => {
    const matrix = await render()
    const failed = matrix.findAll('tbody tr')[1]!.find('[data-status="failed"]')
    expect(failed.attributes('href')).toBe('/evaluations/suite%201/cases/case%2F2?check=answer_correctness')
    expect(matrix.find('tbody th a').attributes('href')).toBe('/evaluations/suite%201/cases/qa-1')
  })

  it('leaves a check that did not run without a link', async () => {
    const matrix = await render()
    const lastCell = matrix.findAll('tbody tr')[2]!.findAll('td')[9]!
    expect(lastCell.classes()).toContain('not-run')
    expect(lastCell.find('a').exists()).toBe(false)
  })

  it('prints passed / applicable per check, and n/a when a check never applied', async () => {
    const matrix = await render()
    const footer = matrix.findAll('tfoot td')
    expect(footer[0]!.text()).toBe('2/3')
    expect(footer[7]!.text()).toBe('n/a')
  })

  it('has a single tab stop and moves with the arrow keys', async () => {
    const matrix = await render()
    const focusable = matrix.findAll('a').filter((a) => a.attributes('tabindex') === '0')
    expect(focusable).toHaveLength(1)
    const grid = matrix.find('table')
    await grid.trigger('keydown', { key: 'ArrowRight' })
    await flushPromises()
    expect(document.activeElement?.getAttribute('data-cell')).toBe('0:1')
    await grid.trigger('keydown', { key: 'ArrowDown' })
    await flushPromises()
    expect(document.activeElement?.getAttribute('data-cell')).toBe('1:1')
    await grid.trigger('keydown', { key: 'End' })
    await flushPromises()
    expect(document.activeElement?.getAttribute('data-cell')).toBe('1:10')
    await grid.trigger('keydown', { key: 'ArrowDown' }) // the only cell below did not run, so focus stays
    await flushPromises()
    expect(document.activeElement?.getAttribute('data-cell')).toBe('1:10')
    await grid.trigger('keydown', { key: 'Home' })
    await flushPromises()
    expect(document.activeElement?.getAttribute('data-cell')).toBe('1:0')
  })

  it('keeps its tab stop when the rows change under the cursor', async () => {
    const matrix = await render()
    const grid = matrix.find('table')
    await grid.trigger('keydown', { key: 'ArrowDown' })
    await grid.trigger('keydown', { key: 'ArrowDown' })
    await flushPromises()
    expect(document.activeElement?.getAttribute('data-cell')).toBe('2:0')
    // e.g. "Only failing cases" leaves one row.
    await matrix.setProps({ cases: [caseResult('case/2', ['answer_correctness'])] })
    const focusable = matrix.findAll('a').filter((a) => a.attributes('tabindex') === '0')
    expect(focusable.map((a) => a.attributes('data-cell'))).toEqual(['0:0'])
  })
})
