import { ref, watch, type Ref } from 'vue'

/**
 * A per-browser boolean preference (a dismissed card, say). A convenience only: when storage
 * is blocked it lasts for this page view, and nothing depends on it being kept.
 */
export function useLocalFlag(key: string): Ref<boolean> {
  let initial = false
  try {
    initial = window.localStorage.getItem(key) === '1'
  } catch {
    /* storage blocked */
  }
  const flag = ref(initial)
  watch(flag, (value) => {
    try {
      if (value) window.localStorage.setItem(key, '1')
      else window.localStorage.removeItem(key)
    } catch {
      /* storage blocked */
    }
  })
  return flag
}
