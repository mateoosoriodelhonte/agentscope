import { mount, type VueWrapper } from '@vue/test-utils'
import { afterEach, describe, expect, it } from 'vitest'
import { defineComponent, nextTick, ref } from 'vue'
import Modal from '../Modal.vue'

let wrapper: VueWrapper | null = null

const Host = defineComponent({
  components: { Modal },
  props: { busy: { type: Boolean, default: false } },
  setup() {
    const open = ref(false)
    return { open }
  },
  template: `
    <button class="opener" @click="open = true">Open</button>
    <Modal :open="open" title="Things" :busy="busy" @close="open = false">
      <input class="first" />
      <input class="second" data-autofocus />
      <template #footer><button class="last">Done</button></template>
    </Modal>
  `,
})

async function openModal(busy = false) {
  wrapper = mount(Host, { props: { busy }, attachTo: document.body })
  const opener = wrapper.find('.opener')
  ;(opener.element as HTMLElement).focus()
  await opener.trigger('click')
  await nextTick()
  await nextTick()
  return wrapper
}

afterEach(() => {
  wrapper?.unmount()
  wrapper = null
})

describe('Modal', () => {
  it('is a labelled modal dialog that focuses the marked control', async () => {
    const host = await openModal()
    const dialog = host.find('[role="dialog"]')
    expect(dialog.attributes('aria-modal')).toBe('true')
    expect(host.find(`#${dialog.attributes('aria-labelledby')}`).text()).toBe('Things')
    expect(document.activeElement).toBe(host.find('.second').element)
  })

  it('keeps Tab inside the dialog', async () => {
    const host = await openModal()
    const dialog = host.find('[role="dialog"]')
    ;(host.find('.last').element as HTMLElement).focus()
    await dialog.trigger('keydown', { key: 'Tab' })
    expect(document.activeElement).toBe(host.find('.close').element)
    await dialog.trigger('keydown', { key: 'Tab', shiftKey: true })
    expect(document.activeElement).toBe(host.find('.last').element)
  })

  it('closes on Escape and returns focus to the opener', async () => {
    const host = await openModal()
    await host.find('[role="dialog"]').trigger('keydown', { key: 'Escape' })
    await nextTick()
    expect(host.find('[role="dialog"]').exists()).toBe(false)
    expect(document.activeElement).toBe(host.find('.opener').element)
  })

  it('cannot be closed while busy', async () => {
    const host = await openModal(true)
    await host.find('[role="dialog"]').trigger('keydown', { key: 'Escape' })
    await host.find('.backdrop').trigger('click')
    expect(host.find('[role="dialog"]').exists()).toBe(true)
  })
})
