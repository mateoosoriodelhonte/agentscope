import { computed, type ComputedRef } from 'vue'
import { useRoute, useRouter, type LocationQuery, type LocationQueryValue } from 'vue-router'

/** Converts one URL query parameter to a typed value and back. */
export interface QueryCodec<T> {
  parse(raw: string | undefined): T
  /** `undefined` removes the parameter, so defaults never clutter the URL. */
  serialize(value: T): string | undefined
}

export type QuerySchema<S> = { [K in keyof S]: QueryCodec<S[K]> }

export interface QueryState<S> {
  state: ComputedRef<S>
  set(patch: Partial<S>, options?: { push?: boolean }): Promise<unknown>
}

function first(value: LocationQueryValue | LocationQueryValue[] | undefined): string | undefined {
  const single = Array.isArray(value) ? value[0] : value
  return single ?? undefined
}

/**
 * View state held in the URL, so every screen state is linkable and survives reloads.
 * Updates replace the history entry unless `push` is set.
 */
export function useQueryState<S extends object>(schema: QuerySchema<S>): QueryState<S> {
  const route = useRoute()
  const router = useRouter()
  const keys = Object.keys(schema) as (keyof S & string)[]

  const state = computed(() => {
    const parsed = {} as S
    for (const key of keys) parsed[key] = schema[key].parse(first(route.query[key]))
    return parsed
  })

  // Navigation is asynchronous: patches made before it settles build on each other.
  let pending: LocationQuery | null = null

  function set(patch: Partial<S>, options: { push?: boolean } = {}): Promise<unknown> {
    const query: LocationQuery = { ...(pending ?? route.query) }
    for (const key of Object.keys(patch) as (keyof S & string)[]) {
      const value = schema[key].serialize(patch[key] as S[typeof key])
      if (value === undefined) delete query[key]
      else query[key] = value
    }
    pending = query
    const navigation = options.push ? router.push({ query }) : router.replace({ query })
    return navigation.finally(() => {
      if (pending === query) pending = null
    })
  }

  return { state, set }
}

export const param = {
  string(): QueryCodec<string | null> {
    return { parse: (raw) => raw || null, serialize: (value) => value ?? undefined }
  },
  enum<T extends string>(values: readonly T[]): QueryCodec<T | null> {
    return {
      parse: (raw) => (values.includes(raw as T) ? (raw as T) : null),
      serialize: (value) => value ?? undefined,
    }
  },
  boolean(): QueryCodec<boolean | null> {
    return {
      parse: (raw) => (raw === 'true' ? true : raw === 'false' ? false : null),
      serialize: (value) => (value === null ? undefined : String(value)),
    }
  },
  int(fallback: number, min = 0): QueryCodec<number> {
    return {
      parse: (raw) => {
        const n = raw === undefined ? NaN : Number(raw)
        return Number.isInteger(n) && n >= min ? n : fallback
      },
      serialize: (value) => (value === fallback ? undefined : String(value)),
    }
  },
}
