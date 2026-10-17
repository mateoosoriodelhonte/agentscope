import { onBeforeUnmount, ref, shallowRef, watch, type Ref, type ShallowRef, type WatchSource } from 'vue'

export interface Resource<T> {
  data: ShallowRef<T | null>
  error: ShallowRef<unknown>
  /** True until the first response (or failure) of the current load. */
  loading: Ref<boolean>
  reload(): Promise<void>
}

/**
 * Loads one API resource for a screen. A new load aborts the previous one, unmounting aborts
 * the last, and reloading keeps the old data on screen until the new data arrives. Pass a
 * `source` to reload whenever it changes (a route parameter, say).
 */
export function useResource<T>(
  fetcher: (signal: AbortSignal) => Promise<T>,
  source?: WatchSource<unknown>,
): Resource<T> {
  const data = shallowRef<T | null>(null)
  const error = shallowRef<unknown>(null)
  const loading = ref(true)
  let controller: AbortController | null = null

  async function reload(): Promise<void> {
    controller?.abort()
    const current = new AbortController()
    controller = current
    loading.value = true
    try {
      const value = await fetcher(current.signal)
      if (current.signal.aborted) return
      data.value = value
      error.value = null
    } catch (e) {
      if (current.signal.aborted) return
      error.value = e
    } finally {
      if (!current.signal.aborted) loading.value = false
    }
  }

  if (source) {
    watch(
      source,
      () => {
        data.value = null
        void reload()
      },
      { immediate: true },
    )
  } else {
    void reload()
  }
  onBeforeUnmount(() => controller?.abort())
  return { data, error, loading, reload }
}
