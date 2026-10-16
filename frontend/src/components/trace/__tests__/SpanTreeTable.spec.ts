import { mount, type VueWrapper } from '@vue/test-utils'
import { afterEach, describe, expect, it } from 'vitest'
import { loadFixture } from '@/__tests__/fixtures'
import { useTraceModel, type TraceModel } from '@/composables/useTraceModel'
import SpanTreeTable from '../SpanTreeTable.vue'

let wrapper: VueWrapper | null = null

function mountTree(model: TraceModel, extra: Record<string, unknown> = {}) {
  const tree: VueWrapper = mount(SpanTreeTable, {
    attachTo: document.body,
    props: {
      model,
      mode: 'relative',
      selected: model.runRowId!,
      expanded: [],
      'onUpdate:selected': (id: string) => tree.setProps({ selected: id }),
      'onUpdate:expanded': (ids: string[]) => tree.setProps({ expanded: ids }),
      ...extra,
    },
  })
  wrapper = tree
  return tree
}

const rows = (tree: VueWrapper) => tree.findAll('tbody tr')
const expandedIds = (tree: VueWrapper) =>
  tree.findAll('tr[aria-expanded="true"]').map((r) => r.attributes('id')!.replace('trace-row-', ''))
const selectedLabel = (tree: VueWrapper) =>
  tree.find('tr[aria-selected="true"] .label').text()

async function press(tree: VueWrapper, key: string) {
  await tree.find('[role="treegrid"]').trigger('keydown', { key })
}

afterEach(() => {
  wrapper?.unmount()
  wrapper = null
})

describe('SpanTreeTable', () => {
  const model = useTraceModel(loadFixture('run-v1').trace)

  it('exposes a treegrid with levels, set positions and a roving tabindex', () => {
    const tree = mountTree(model)
    expect(tree.find('table').attributes('role')).toBe('treegrid')
    const all = rows(tree)
    expect(all.map((r) => r.find('.label').text())).toEqual(['Run', 'Step 1', 'Step 2', 'Step 3', 'Step 4', 'Finish'])
    const step1 = all[1]!
    expect(step1.attributes()).toMatchObject({
      role: 'row',
      'aria-level': '1',
      'aria-expanded': 'false',
      'aria-posinset': '2',
      'aria-setsize': '6',
      'aria-selected': 'false',
      tabindex: '-1',
    })
    // The run row has no children, so it is not expandable.
    expect(all[0]!.attributes('aria-expanded')).toBeUndefined()
    expect(all.filter((r) => r.attributes('tabindex') === '0')).toHaveLength(1)
    expect(all[0]!.attributes('tabindex')).toBe('0')
    // Narratives are part of the row; bars are hidden from assistive technology.
    expect(step1.text()).toContain('Called search_documents → ok (3 results)')
    expect(step1.find('.track').attributes('aria-hidden')).toBe('true')
  })

  it('navigates with arrows, j/k, Home and End, expanding and collapsing branches', async () => {
    const tree = mountTree(model)
    await press(tree, 'ArrowDown')
    expect(selectedLabel(tree)).toBe('Step 1')
    await press(tree, 'ArrowRight')
    expect(expandedIds(tree)).toEqual(['step-1'])
    expect(rows(tree)[1]!.attributes('aria-expanded')).toBe('true')
    await press(tree, 'ArrowRight') // already open: move to the first child
    expect(selectedLabel(tree)).toBe('plan')
    expect(tree.find('tr[aria-selected="true"]').attributes('aria-level')).toBe('2')
    await press(tree, 'j')
    expect(selectedLabel(tree)).toBe('guard')
    await press(tree, 'k')
    await press(tree, 'ArrowLeft') // a closed child moves to its parent
    expect(selectedLabel(tree)).toBe('Step 1')
    await press(tree, 'ArrowLeft') // an open parent collapses
    expect(expandedIds(tree)).toEqual([])
    await press(tree, 'End')
    expect(selectedLabel(tree)).toBe('Finish')
    await press(tree, 'Home')
    expect(selectedLabel(tree)).toBe('Run')
    await press(tree, 'ArrowDown')
    await press(tree, 'Enter')
    expect(expandedIds(tree)).toEqual(['step-1'])
  })

  it('moves focus with the selection while the tree has focus', async () => {
    const tree = mountTree(model)
    ;(rows(tree)[0]!.element as HTMLElement).focus()
    await press(tree, 'ArrowDown')
    await tree.vm.$nextTick()
    expect(document.activeElement?.id).toBe('trace-row-step-1')
    expect(document.activeElement?.getAttribute('tabindex')).toBe('0')
  })

  it('selects on click and toggles on the disclosure glyph', async () => {
    const tree = mountTree(model)
    await rows(tree)[3]!.trigger('click')
    expect(selectedLabel(tree)).toBe('Step 3')
    await rows(tree)[3]!.find('.twisty').trigger('click')
    expect(expandedIds(tree)).toEqual(['step-3'])
  })

  it('outlines evidence spans and marks collapsed steps that contain them', async () => {
    const chat = model.rows.find((r) => r.kind === 'llm' && r.groupId === 'step-4')!
    const tree = mountTree(model, {
      outlined: new Set([chat.id]),
      citedBy: new Map([[chat.id, 2]]),
    })
    expect(rows(tree)[4]!.classes()).toContain('has-evidence')
    await tree.setProps({ expanded: ['step-4', chat.parentId!] })
    const row = tree.find(`#trace-row-${chat.id}`)
    expect(row.classes()).toContain('evidence')
    expect(row.text()).toContain('cited ×2')
  })

  it('keeps the selected row reachable when its branch is collapsed', () => {
    const plan = model.rows.find((r) => r.label === 'plan')!
    const tree = mountTree(model, { selected: plan.id })
    expect(tree.find('#trace-row-step-1').attributes('tabindex')).toBe('0')
  })
})
