import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import type { SpanRecord } from '@/api/types'
import SpanTree from '../SpanTree.vue'

function span(id: string, parent: string | null, name: string, start: string): SpanRecord {
  return {
    trace_id: 't'.repeat(32),
    span_id: id,
    parent_span_id: parent,
    name,
    kind: 'node',
    status: 'ok',
    status_message: null,
    start_time: start,
    end_time: start,
    duration_ms: 1,
    attributes: {},
    input: null,
    output: null,
    events: [],
  }
}

describe('SpanTree', () => {
  it('renders spans depth-first in start order and emits selection', async () => {
    const spans = [
      span('b', 'a', 'node plan', '2026-01-01T00:00:01Z'),
      span('a', null, 'invoke_agent', '2026-01-01T00:00:00Z'),
      span('c', 'a', 'node guard', '2026-01-01T00:00:02Z'),
    ]
    const wrapper = mount(SpanTree, { props: { spans, selected: null } })
    const names = wrapper.findAll('[role="treeitem"]').map((li) => li.text())
    expect(names[0]).toContain('invoke_agent')
    expect(names[1]).toContain('node plan')
    expect(names[2]).toContain('node guard')
    await wrapper.findAll('[role="treeitem"]')[2]!.trigger('click')
    expect(wrapper.emitted('select')?.[0]).toEqual(['c'])
  })
})
