/**
 * The single registry of trace-explorer shortcuts. Handlers and the `?` overlay both read
 * it, so the help can never drift from the behaviour. Single-key shortcuts only fire while
 * focus is inside the trace region (WCAG 2.1.4).
 */

export type TraceAction =
  | 'next'
  | 'prev'
  | 'expand'
  | 'collapse'
  | 'first'
  | 'last'
  | 'toggle'
  | 'expandAll'
  | 'collapseAll'
  | 'nextEvidence'
  | 'prevEvidence'
  | 'escape'
  | 'tab'
  | 'toggleMode'
  | 'copyId'
  | 'help'

export interface KeyBinding {
  keys: readonly string[]
  action: TraceAction
  group: 'Navigate' | 'Evidence' | 'View'
  description: string
}

export const TRACE_BINDINGS: readonly KeyBinding[] = [
  { keys: ['j', 'ArrowDown'], action: 'next', group: 'Navigate', description: 'Next row' },
  { keys: ['k', 'ArrowUp'], action: 'prev', group: 'Navigate', description: 'Previous row' },
  { keys: ['ArrowRight'], action: 'expand', group: 'Navigate', description: 'Expand, or go to the first child' },
  { keys: ['ArrowLeft'], action: 'collapse', group: 'Navigate', description: 'Collapse, or go to the parent' },
  { keys: ['Home'], action: 'first', group: 'Navigate', description: 'First row' },
  { keys: ['End'], action: 'last', group: 'Navigate', description: 'Last visible row' },
  { keys: ['Enter'], action: 'toggle', group: 'Navigate', description: 'Expand or collapse the row' },
  { keys: [']'], action: 'expandAll', group: 'Navigate', description: 'Expand every step' },
  { keys: ['['], action: 'collapseAll', group: 'Navigate', description: 'Collapse every step' },
  { keys: ['n'], action: 'nextEvidence', group: 'Evidence', description: 'Next evidence span of the active check' },
  { keys: ['N'], action: 'prevEvidence', group: 'Evidence', description: 'Previous evidence span' },
  { keys: ['Escape'], action: 'escape', group: 'Evidence', description: 'Clear the active check' },
  { keys: ['1', '2', '3', '4'], action: 'tab', group: 'View', description: 'Switch detail tab' },
  { keys: ['t'], action: 'toggleMode', group: 'View', description: 'Duration bars: relative / timeline' },
  { keys: ['c'], action: 'copyId', group: 'View', description: 'Copy the selected span id' },
  { keys: ['?'], action: 'help', group: 'View', description: 'Show these shortcuts' },
]

/** Keys that keep their usual meaning (tabs, separators, buttons) outside the tree. */
export const TREE_ONLY_KEYS: ReadonlySet<string> = new Set([
  'ArrowUp',
  'ArrowDown',
  'ArrowLeft',
  'ArrowRight',
  'Home',
  'End',
  'Enter',
])

export const TREE_ACTIONS: ReadonlySet<TraceAction> = new Set([
  'next',
  'prev',
  'expand',
  'collapse',
  'first',
  'last',
  'toggle',
])

const KEY_LABELS: Record<string, string> = {
  ArrowDown: '↓',
  ArrowUp: '↑',
  ArrowLeft: '←',
  ArrowRight: '→',
  Escape: 'Esc',
  N: 'Shift N',
}

export function keyLabel(key: string): string {
  return KEY_LABELS[key] ?? key
}

function isEditable(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false
  return target.isContentEditable || ['INPUT', 'TEXTAREA', 'SELECT'].includes(target.tagName)
}

export function matchBinding(event: KeyboardEvent): KeyBinding | undefined {
  if (event.ctrlKey || event.metaKey || event.altKey || isEditable(event.target)) return undefined
  return TRACE_BINDINGS.find((binding) => binding.keys.includes(event.key))
}
