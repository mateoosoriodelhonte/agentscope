import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import JsonView from '../JsonView.vue'
import { jsonLines } from '../jsonLines'
import RedactedValue from '../RedactedValue.vue'

const PAYLOAD = '<img src=x onerror=alert(1)>'

describe('JsonView', () => {
  it('renders hostile strings as text without creating elements', () => {
    const view = mount(JsonView, {
      props: { value: { [PAYLOAD]: PAYLOAD, nested: ['<svg onload=alert(2)>', { a: '<script>x</script>' }] } },
      attachTo: document.body,
    })
    const root = view.element as HTMLElement
    expect(root.querySelector('img, svg, script')).toBeNull()
    expect(document.querySelector('img, script')).toBeNull()
    expect(root.textContent).toContain(`"${PAYLOAD}": "${PAYLOAD}"`)
    expect(root.textContent).toContain('"<svg onload=alert(2)>"')
    view.unmount()
  })

  it('renders a bare hostile string as a single text value', () => {
    const view = mount(JsonView, { props: { value: PAYLOAD } })
    expect(view.find('img').exists()).toBe(false)
    expect(view.find('.string').text()).toBe(`"${PAYLOAD}"`)
  })

  it('pretty-prints with typed tokens', () => {
    const view = mount(JsonView, { props: { value: { n: 3, ok: true, none: null, list: [] } } })
    expect(view.text()).toBe('{\n  "n": 3,\n  "ok": true,\n  "none": null,\n  "list": []\n}')
    expect(view.find('.number').text()).toBe('3')
    expect(view.find('.boolean').text()).toBe('true')
    expect(view.find('.null').text()).toBe('null')
  })

  it('shows redaction and truncation markers distinctly', () => {
    const view = mount(JsonView, {
      props: { value: { api_key: '[REDACTED]', note: 'key [REDACTED:openai_key] used', long: 'abc… [truncated 12 chars]' } },
    })
    const redacted = view.findAllComponents(RedactedValue)
    expect(redacted.map((r) => r.text())).toEqual(['[REDACTED]', '[REDACTED:openai_key]'])
    expect(redacted[1]!.attributes('title')).toContain('openai key')
    expect(view.find('.truncated').text()).toBe('… [truncated 12 chars]')
  })
})

describe('jsonLines', () => {
  it('splits strings around markers and keeps the structure', () => {
    expect(jsonLines({ k: 'a [REDACTED] b' })).toEqual([
      { depth: 0, tokens: [{ kind: 'punct', text: '{' }] },
      {
        depth: 1,
        tokens: [
          { kind: 'key', text: '"k"' },
          { kind: 'punct', text: ': ' },
          { kind: 'string', text: '"a ' },
          { kind: 'redacted', text: '[REDACTED]' },
          { kind: 'string', text: ' b"' },
        ],
      },
      { depth: 0, tokens: [{ kind: 'punct', text: '}' }] },
    ])
  })
})
