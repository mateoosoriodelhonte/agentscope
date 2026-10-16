import { computed, ref, watchEffect, type ComputedRef, type Ref } from 'vue'

export type ThemePreference = 'system' | 'light' | 'dark'
export type Theme = 'light' | 'dark'

// Must match public/theme-init.js, which applies the theme before the app loads.
const STORAGE_KEY = 'agentscope.theme'

function readPreference(): ThemePreference {
  try {
    const stored = window.localStorage.getItem(STORAGE_KEY)
    return stored === 'light' || stored === 'dark' ? stored : 'system'
  } catch {
    return 'system' // storage blocked: follow the system
  }
}

function writePreference(value: ThemePreference): void {
  try {
    if (value === 'system') window.localStorage.removeItem(STORAGE_KEY)
    else window.localStorage.setItem(STORAGE_KEY, value)
  } catch {
    /* storage blocked: the choice lasts for this page only */
  }
}

interface ThemeState {
  preference: Ref<ThemePreference>
  theme: ComputedRef<Theme>
  setPreference(value: ThemePreference): void
}

let state: ThemeState | null = null

/** One shared theme state for the app: a manual choice, or the system setting. */
export function useTheme(): ThemeState {
  if (state) return state
  const query =
    typeof window.matchMedia === 'function' ? window.matchMedia('(prefers-color-scheme: dark)') : null
  const systemDark = ref(query?.matches ?? false)
  query?.addEventListener('change', (event) => {
    systemDark.value = event.matches
  })
  const preference = ref<ThemePreference>(readPreference())
  const theme = computed<Theme>(() =>
    preference.value === 'system' ? (systemDark.value ? 'dark' : 'light') : preference.value,
  )
  watchEffect(() => {
    document.documentElement.dataset.theme = theme.value
  })
  state = {
    preference,
    theme,
    setPreference(value) {
      preference.value = value
      writePreference(value)
    },
  }
  return state
}
