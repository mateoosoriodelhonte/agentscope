// Mirrors agentscope.domain contracts (Pydantic). Kept in sync by API integration tests.

export type RunStatus = 'running' | 'completed' | 'failed' | 'max_steps' | 'timeout' | 'error'
export type SpanKind = 'run' | 'node' | 'llm' | 'tool' | 'guardrail'
export type SpanStatus = 'unset' | 'ok' | 'error'
export type CheckStatus = 'passed' | 'failed' | 'not_applicable'
export type JsonValue = string | number | boolean | null | JsonValue[] | { [key: string]: JsonValue }

export interface SpanEvent {
  name: string
  timestamp: string
  attributes: Record<string, JsonValue>
}

export interface SpanRecord {
  trace_id: string
  span_id: string
  parent_span_id: string | null
  name: string
  kind: SpanKind
  status: SpanStatus
  status_message: string | null
  start_time: string
  end_time: string
  duration_ms: number
  attributes: Record<string, JsonValue>
  input: JsonValue
  output: JsonValue
  events: SpanEvent[]
}

export interface StateTransition {
  seq: number
  step: number
  source: string
  target: string
  reason: string
  timestamp: string
}

export interface ToolInvocation {
  call_id: string
  step: number
  tool: string
  arguments: Record<string, JsonValue>
  permitted: boolean
  executed: boolean
  denial_reason: string | null
  outcome: string
  span_id: string | null
}

export interface GuardrailEvent {
  kind: string
  step: number
  detail: string
  tool: string | null
  span_id: string | null
}

export interface TokenUsage {
  input_tokens: number | null
  output_tokens: number | null
  reported_calls: number
  total_calls: number
}

export interface TraceRecord {
  trace_id: string
  run_id: string
  agent: string
  agent_version: string
  config_hash: string
  task: string
  status: RunStatus
  error_code: string | null
  error: string | null
  started_at: string
  duration_ms: number
  steps: number
  provider: string
  model: string
  final_output: JsonValue
  usage: TokenUsage | null
  spans: SpanRecord[]
  transitions: StateTransition[]
  tool_invocations: ToolInvocation[]
  guardrail_events: GuardrailEvent[]
}

export interface Evidence {
  role: 'violation' | 'support' | 'absent'
  detail: string
  span_id: string | null
}

export interface CheckResult {
  name: string
  status: CheckStatus
  reason: string
  message: string
  expected: JsonValue
  actual: JsonValue
  evidence: Evidence[]
}

export interface CaseResult {
  case_id: string
  passed: boolean
  checks: CheckResult[]
}

export interface RunDetail {
  trace: TraceRecord
  evaluation: CaseResult | null
}

export interface RunSummary {
  run_id: string
  agent: string
  agent_version: string
  provider: string
  model: string
  status: RunStatus
  task: string
  started_at: string
  duration_ms: number
  steps: number
  tool_calls: number
  blocked_tool_calls: number
  guardrail_events: number
  passed: boolean | null
}

export interface RunList {
  items: RunSummary[]
  total: number
}
