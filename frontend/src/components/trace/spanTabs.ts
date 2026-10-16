import type { TabItem } from '@/components/ui/Tabs.vue'
import { attr, ATTR, nodeName, type TraceRow } from '@/composables/useTraceModel'

const TAB = {
  run: { id: 'run', label: 'Run' },
  step: { id: 'step', label: 'Step' },
  outcome: { id: 'outcome', label: 'Outcome' },
  decision: { id: 'decision', label: 'Decision' },
  messages: { id: 'messages', label: 'Messages' },
  verdict: { id: 'verdict', label: 'Verdict' },
  findings: { id: 'findings', label: 'Findings' },
  call: { id: 'call', label: 'Call' },
  result: { id: 'result', label: 'Result' },
  attempts: { id: 'attempts', label: 'Attempts' },
  observation: { id: 'observation', label: 'Observation' },
  validation: { id: 'validation', label: 'Validation' },
  raw: { id: 'raw', label: 'Raw' },
} satisfies Record<string, TabItem>

/** Detail tabs depend on what the row is; every span also gets a Raw tab. */
export function detailTabs(row: TraceRow): TabItem[] {
  switch (row.kind) {
    case 'run':
      return [TAB.run, TAB.raw]
    case 'step':
      return [TAB.step]
    case 'finish':
      return [TAB.outcome]
    case 'llm':
      return [TAB.decision, TAB.messages, TAB.raw]
    case 'tool':
      return [TAB.call, TAB.result, TAB.attempts, TAB.raw]
    case 'guardrail':
      return attr(row.span, ATTR.allowed) === undefined ? [TAB.findings, TAB.raw] : [TAB.verdict, TAB.raw]
    case 'node':
      break
  }
  switch (row.span ? nodeName(row.span) : '') {
    case 'plan':
      return [TAB.decision, TAB.messages, TAB.raw]
    case 'guard':
      return [TAB.verdict, TAB.raw]
    case 'act':
      return [TAB.call, TAB.result, TAB.attempts, TAB.raw]
    case 'observe':
      return [TAB.observation, TAB.raw]
    case 'validate':
      return [TAB.validation, TAB.raw]
    case 'finish':
      return [TAB.outcome, TAB.raw]
    default:
      return [TAB.raw]
  }
}
