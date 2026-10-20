import type { ConfigDimension } from '@/api/types'

/** Behaviour dimensions in the order the configuration diff lists them. */
export const DIMENSION_ORDER: readonly ConfigDimension[] = [
  'prompt',
  'model',
  'tool_schema',
  'graph',
  'retry',
  'isolation',
  'tools',
  'limits',
  'other',
]

export const DIMENSION_LABELS: Record<ConfigDimension, string> = {
  prompt: 'Prompt',
  model: 'Model',
  tool_schema: 'Tool schemas',
  graph: 'Graph',
  retry: 'Retry policy',
  isolation: 'Untrusted-output isolation',
  tools: 'Permitted tools',
  limits: 'Limits',
  other: 'Other',
}
