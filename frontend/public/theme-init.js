// Applies the saved (or system) colour theme before first paint, so dark-mode users never
// see a light flash. It is an external file because the CSP forbids inline scripts; the key
// must match src/composables/useTheme.ts.
;(function () {
  var preference = null
  try {
    preference = window.localStorage.getItem('agentscope.theme')
  } catch {
    // Storage can be blocked (private windows, strict settings): fall back to the system.
  }
  var systemDark =
    typeof window.matchMedia === 'function' &&
    window.matchMedia('(prefers-color-scheme: dark)').matches
  var dark = preference === 'dark' || (preference !== 'light' && systemDark)
  document.documentElement.setAttribute('data-theme', dark ? 'dark' : 'light')
})()
