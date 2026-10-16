import { nextTick } from 'vue'

/**
 * Loading, error and loaded states each render their own h1. When the router has moved
 * focus to the current heading, keep it on the page heading across the swap instead of
 * dropping it to <body>.
 */
export async function keepHeadingFocus(root: HTMLElement | null, swap: () => void): Promise<void> {
  const heading = root?.querySelector('h1')
  const focused = heading != null && heading === document.activeElement
  swap()
  if (!focused) return
  await nextTick()
  root?.querySelector<HTMLElement>('h1')?.focus()
}
