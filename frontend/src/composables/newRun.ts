import { shallowReactive } from 'vue'
import type { ExpectedAnswer, FaultSpec, ModelFaultSpec } from '@/api/types'

/** What the New run drawer starts from, e.g. an agent version page or a dataset case. */
export interface NewRunPrefill {
  /** A built-in version, e.g. research_assistant@v2. */
  agentRef?: string
  /** An agent name without a version (a dataset case's agent): its latest version is chosen. */
  agent?: string
  task?: string
  faults?: FaultSpec[]
  modelFaults?: ModelFaultSpec[]
  expected?: ExpectedAnswer | null
  /** Where the prefill came from, shown in the drawer (e.g. "case c3 of qa@1"). */
  source?: string
}

/**
 * One drawer for the whole app (mounted in App.vue), opened from Agents, Runs and dataset
 * case rows. Each open starts a fresh form from its prefill.
 */
export const newRun = shallowReactive<{ open: boolean; prefill: NewRunPrefill; opened: number }>({
  open: false,
  prefill: {},
  opened: 0,
})

export function openNewRun(prefill: NewRunPrefill = {}): void {
  newRun.prefill = prefill
  newRun.opened += 1
  newRun.open = true
}

export function closeNewRun(): void {
  newRun.open = false
}
