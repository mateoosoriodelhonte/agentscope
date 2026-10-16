/*
 * TypeScript mirror of the HTTP contract in src/agentscope/api/schemas.py and of every
 * domain model it references (src/agentscope/domain/*). Names are identical to the Pydantic
 * models. Response fields are always present (Pydantic serialises defaults and None as
 * null); request-only models mark defaulted fields optional.
 */

export type JsonValue = string | number | boolean | null | JsonValue[] | { [key: string]: JsonValue }

/** ISO 8601 date-time string as serialised by Pydantic. */
export type DateTime = string

// --- domain/tools.py ---------------------------------------------------------------------

export const TOOL_OUTCOMES = [
  'ok',
  'error',
  'timeout',
  'malformed',
  'permission_denied',
  'invalid_arguments',
  'unknown_tool',
] as const
export type ToolOutcome = (typeof TOOL_OUTCOMES)[number]

export type SideEffect = 'none' | 'sandbox_write'

export const FAULT_KINDS = [
  'timeout',
  'error',
  'malformed',
  'incomplete',
  'misleading',
  'injection',
  'permission_denied',
] as const
export type FaultKind = (typeof FAULT_KINDS)[number]

export interface ToolSpec {
  name: string
  version: number
  description: string
  parameters: Record<string, JsonValue>
  side_effect: SideEffect
}

export interface FaultSpec {
  tool: string
  kind: FaultKind
  on_call: number | null
  times: number | null
  payload: string | null
}

export const MODEL_FAULT_KINDS = ['invalid_json', 'empty', 'schema_violation', 'unknown_tool'] as const
export type ModelFaultKind = (typeof MODEL_FAULT_KINDS)[number]

export interface ModelFaultSpec {
  kind: ModelFaultKind
  on_call: number
  times: number
}

export interface ToolCall {
  call_id: string
  tool: string
  arguments: Record<string, JsonValue>
}

export interface ToolResult {
  call_id: string
  tool: string
  attempt: number
  outcome: ToolOutcome
  output: JsonValue
  error: string | null
  duration_ms: number
  fault: FaultKind | null
}

// --- domain/agents.py --------------------------------------------------------------------

export type ProviderName = 'fake' | 'ollama' | 'openai_compatible'
export type GraphVariant = 'basic' | 'validated'

export interface ModelConfig {
  provider: ProviderName
  model: string
  temperature: number
  seed: number | null
  max_output_tokens: number
  request_timeout_s: number
}

export interface RetryPolicy {
  max_tool_retries: number
  retry_on: ToolOutcome[]
  max_decision_repairs: number
  max_output_repairs: number
}

export interface Limits {
  max_steps: number
  tool_timeout_s: number
  run_timeout_s: number
  max_tool_output_chars: number
}

export interface AgentConfig {
  agent: string
  version: string
  label: string
  description: string
  prompt_version: string
  model: ModelConfig
  tools: string[]
  tool_schema_version: number
  graph: GraphVariant
  isolate_untrusted: boolean
  retry: RetryPolicy
  limits: Limits
}

// --- domain/datasets.py ------------------------------------------------------------------

export const MATCH_MODES = ['exact', 'normalized', 'contains', 'numeric', 'one_of'] as const
export type MatchMode = (typeof MATCH_MODES)[number]

export interface ExpectedAnswer {
  field: string
  match: MatchMode
  value: JsonValue
  values: JsonValue[] | null
  tolerance: number | null
  max_chars: number | null
}

export interface ArgMatcher {
  equals: JsonValue
  contains: string | null
  one_of: JsonValue[] | null
}

export interface ExpectedToolCall {
  tool: string
  arguments: Record<string, ArgMatcher>
  min_calls: number
  max_calls: number | null
}

export type OutputStatus =
  | 'answered'
  | 'insufficient_evidence'
  | 'cannot_complete'
  | 'refused'
  | 'planned'

export interface SuccessCriteria {
  must_complete: boolean
  expected_output_status: OutputStatus | null
  expect_recovery: boolean | null
  max_steps_budget: number | null
  max_tool_calls: number | null
}

export interface LimitsOverride {
  max_steps: number | null
  tool_timeout_s: number | null
  run_timeout_s: number | null
}

export interface EvalCase {
  id: string
  title: string
  task: string
  agent: string | null
  expected: ExpectedAnswer | null
  permitted_tools: string[] | null
  expected_tool_calls: ExpectedToolCall[]
  tool_order: string[]
  tool_order_strict: boolean
  forbidden_tools: string[]
  forbidden_output_substrings: string[]
  faults: FaultSpec[]
  model_faults: ModelFaultSpec[]
  success_criteria: SuccessCriteria
  limits: LimitsOverride | null
  tags: string[]
}

export interface Dataset {
  schema_version: 1
  name: string
  version: string
  description: string
  agent: string | null
  cases: EvalCase[]
}

// --- domain/traces.py --------------------------------------------------------------------

export type SpanKind = 'run' | 'node' | 'llm' | 'tool' | 'guardrail'
export type SpanStatus = 'unset' | 'ok' | 'error'

export const RUN_STATUSES = ['running', 'completed', 'failed', 'max_steps', 'timeout', 'error'] as const
export type RunStatus = (typeof RUN_STATUSES)[number]

export type ErrorCode =
  | 'provider_error'
  | 'provider_timeout'
  | 'invalid_decision'
  | 'invalid_output'
  | 'max_steps'
  | 'run_timeout'
  | 'internal_recursion_limit'
  | 'internal_error'
  | 'interrupted'

export interface SpanEvent {
  name: string
  timestamp: DateTime
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
  start_time: DateTime
  end_time: DateTime
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
  timestamp: DateTime
}

export interface ToolAttempt {
  attempt: number
  outcome: ToolOutcome
  fault: FaultKind | null
  duration_ms: number
  error: string | null
  span_id: string | null
}

export interface ToolInvocation {
  call_id: string
  step: number
  tool: string
  arguments: Record<string, JsonValue>
  permitted: boolean
  executed: boolean
  denial_reason: string | null
  outcome: ToolOutcome
  attempts: ToolAttempt[]
  output: JsonValue
  span_id: string | null
}

export type GuardrailKind =
  | 'tool_denied'
  | 'unknown_tool'
  | 'invalid_arguments'
  | 'suspicious_content'
  | 'invalid_decision'
  | 'decision_repaired'
  | 'output_invalid'
  | 'output_repaired'
  | 'max_steps'
  | 'model_fault_injected'

export interface GuardrailEvent {
  kind: GuardrailKind
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
  source: 'provider_reported'
}

export interface CostEstimate {
  amount: number
  currency: 'USD'
  input_per_1k_tokens: number
  output_per_1k_tokens: number
  partial: boolean
  source: 'estimate_from_configured_rates'
}

export interface TraceRecord {
  trace_id: string
  run_id: string
  agent: string
  agent_version: string
  config_hash: string
  effective_config_hash: string
  config: AgentConfig
  limits: Limits
  permitted_tools: string[]
  task: string
  status: RunStatus
  error_code: ErrorCode | null
  error: string | null
  started_at: DateTime
  ended_at: DateTime
  duration_ms: number
  steps: number
  provider: string
  model: string
  final_output: JsonValue
  usage: TokenUsage | null
  cost: CostEstimate | null
  faults: FaultSpec[]
  model_faults: ModelFaultSpec[]
  case_id: string | null
  spans: SpanRecord[]
  transitions: StateTransition[]
  tool_invocations: ToolInvocation[]
  guardrail_events: GuardrailEvent[]
}

export interface RunSummary {
  run_id: string
  trace_id: string
  agent: string
  agent_version: string
  config_hash: string
  provider: string
  model: string
  status: RunStatus
  error_code: string | null
  task: string
  case_id: string | null
  suite_id: string | null
  started_at: DateTime
  duration_ms: number
  steps: number
  tool_calls: number
  blocked_tool_calls: number
  guardrail_events: number
  faults: number
  passed: boolean | null
}

// --- domain/evaluation.py ----------------------------------------------------------------

/** In the order of the CheckName enum. */
export const CHECK_NAMES = [
  'answer_correctness',
  'schema_validity',
  'required_tool_calls',
  'forbidden_tool_calls',
  'tool_order',
  'tool_arguments',
  'task_completion',
  'timeout_behavior',
  'error_recovery',
  'policy_compliance',
] as const
export type CheckName = (typeof CHECK_NAMES)[number]

export type CheckStatus = 'passed' | 'failed' | 'not_applicable'

export type ReasonCode =
  | 'ok'
  | 'no_expectation'
  | 'output_missing'
  | 'field_missing'
  | 'mismatch'
  | 'type_mismatch'
  | 'schema_invalid'
  | 'call_missing'
  | 'too_many_calls'
  | 'arguments_mismatch'
  | 'order_violated'
  | 'forbidden_attempted'
  | 'unauthorized_execution'
  | 'leaked_content'
  | 'run_not_completed'
  | 'status_mismatch'
  | 'budget_exceeded'
  | 'timeout_not_surfaced'
  | 'not_recovered'
  | 'not_degraded'

export type EvidenceRole = 'violation' | 'support' | 'absent'

export interface Evidence {
  role: EvidenceRole
  detail: string
  span_id: string | null
  step: number | null
  tool: string | null
  call_id: string | null
}

export interface CheckResult {
  name: CheckName
  status: CheckStatus
  reason: ReasonCode
  message: string
  expected: JsonValue
  actual: JsonValue
  evidence: Evidence[]
}

export interface CaseResult {
  case_id: string
  case_hash: string
  run_id: string | null
  trace_id: string | null
  passed: boolean
  status: RunStatus | null
  harness_error: string | null
  checks: CheckResult[]
  steps: number
  duration_ms: number
  tags: string[]
  injected_fault_outcomes: number
  unexpected_tool_errors: number
  invalid_output: boolean
  repaired_output: boolean
  unauthorized_tool_attempts: number
  unauthorized_tool_executions: number
}

export interface CheckStats {
  passed: number
  failed: number
  not_applicable: number
}

export interface UsageSummary {
  input_tokens: number
  output_tokens: number
  reporting_runs: number
  total_runs: number
}

export interface InformationalTimings {
  median_duration_ms: number
  p95_duration_ms: number
}

export interface SuiteMetrics {
  total: number
  passed: number
  failed: number
  harness_errors: number
  pass_rate: number
  run_statuses: Record<string, number>
  checks: Partial<Record<CheckName, CheckStats>>
  injected_fault_outcomes: number
  unexpected_tool_errors: number
  invalid_outputs: number
  repaired_outputs: number
  policy_violation_cases: number
  unauthorized_tool_attempts: number
  unauthorized_tool_executions: number
  avg_steps: number
  usage: UsageSummary | null
  timings: InformationalTimings | null
}

export interface GateThresholds {
  min_pass_rate?: number | null
  max_new_regressions?: number | null
  max_pass_rate_drop_pp?: number | null
  max_invalid_outputs?: number | null
  max_policy_violation_cases?: number | null
  max_unauthorized_tool_executions?: number | null
  max_harness_errors?: number | null
  required_checks?: CheckName[]
}

export interface GateRule {
  name: string
  threshold: JsonValue
  actual: JsonValue
  passed: boolean
  detail: string
}

export interface GateResult {
  passed: boolean
  rules: GateRule[]
  baseline_used: boolean
}

export interface EnvironmentFingerprint {
  agentscope_version: string
  check_engine_version: string
  python_version: string
  langgraph_version: string
  data_hash: string
  provider: string
  model: string
  deterministic: boolean
}

export type SuiteStatus = 'running' | 'completed' | 'error'

export interface SuiteResult {
  suite_id: string
  label: string
  status: SuiteStatus
  dataset_name: string
  dataset_version: string
  dataset_hash: string
  agent_config: AgentConfig
  config_hash: string
  effective_config_hash: string
  checks_enabled: CheckName[]
  environment: EnvironmentFingerprint
  started_at: DateTime
  finished_at: DateTime | null
  cases: CaseResult[]
  metrics: SuiteMetrics
  gate: GateResult | null
}

// --- domain/experiments.py ---------------------------------------------------------------

export type CaseChange =
  | 'regression'
  | 'fix'
  | 'still_passing'
  | 'still_failing_changed'
  | 'still_failing_same'
  | 'added'
  | 'removed'
  | 'content_changed'

export type ConfigDimension =
  | 'prompt'
  | 'model'
  | 'tool_schema'
  | 'graph'
  | 'retry'
  | 'isolation'
  | 'tools'
  | 'limits'
  | 'other'

export interface ConfigDifference {
  path: string
  dimension: ConfigDimension
  a: JsonValue
  b: JsonValue
}

export interface MetricDelta {
  metric: string
  unit: 'count' | 'pp' | 'steps'
  a: number
  b: number
  delta: number
  higher_is_better: boolean
  n: number
}

export interface CheckFlip {
  check: string
  regressions: number
  fixes: number
}

export interface CaseComparison {
  case_id: string
  change: CaseChange
  a_passed: boolean | null
  b_passed: boolean | null
  a_failed_checks: string[]
  b_failed_checks: string[]
  a_run_id: string | null
  b_run_id: string | null
}

export interface SuiteSummary {
  suite_id: string
  agent_ref: string
  config_hash: string
  effective_config_hash: string
  dataset_hash: string
  metrics: SuiteMetrics
}

export interface Comparison {
  experiment_id: string
  name: string
  created_at: DateTime
  dataset_name: string
  dataset_version: string
  dataset_hash: string
  dataset_mismatch: boolean
  deterministic: boolean
  a: SuiteSummary
  b: SuiteSummary
  metric_deltas: MetricDelta[]
  check_flips: CheckFlip[]
  cases: CaseComparison[]
  config_diff: ConfigDifference[]
  confounded: boolean
}

// --- api/schemas.py: agents, tools, providers --------------------------------------------

export interface AgentInfo {
  name: string
  title: string
  description: string
  toolset: string[]
  output_schema: Record<string, JsonValue>
  prompts: Record<string, string>
  versions: AgentConfig[]
}

export interface AgentsResponse {
  agents: AgentInfo[]
  tools: ToolSpec[]
}

export interface ProviderStatus {
  name: string
  enabled: boolean
  deterministic: boolean
  /** null = not probed (disabled); true/false = probe result against the configured target. */
  reachable: boolean | null
  /** host:port only, never credentials. */
  target: string | null
  models: string[]
  detail: string
}

// --- api/schemas.py: jobs ----------------------------------------------------------------

export type JobKind = 'run' | 'evaluation' | 'experiment' | 'demo_seed'
export type JobState = 'queued' | 'running' | 'succeeded' | 'failed'

export interface JobAccepted {
  job_id: string
  kind: JobKind
  /** The id of the resource that will exist when the job succeeds. */
  resource_id: string
}

export interface JobStatus {
  job_id: string
  kind: JobKind
  state: JobState
  resource_id: string
  created_at: DateTime
  finished_at: DateTime | null
  progress: number
  total: number
  error: string | null
}

// --- api/schemas.py: runs ----------------------------------------------------------------

/** A built-in version (`agent_ref`, e.g. `research_assistant@v2`) or a full config. */
export interface AgentSelector {
  agent_ref?: string | null
  config?: AgentConfig | null
}

export interface RunCreate extends AgentSelector {
  task: string
  faults?: FaultSpec[]
  model_faults?: ModelFaultSpec[]
  /** Optional expectation: the run is then evaluated as an ad-hoc test case. */
  expected?: ExpectedAnswer | null
}

export interface RunDetail {
  trace: TraceRecord
  case: EvalCase | null
  evaluation: CaseResult | null
}

export interface RunList {
  items: RunSummary[]
  total: number
}

// --- api/schemas.py: datasets ------------------------------------------------------------

export interface DatasetSummary {
  name: string
  version: string
  content_hash: string
  description: string
  agent: string | null
  case_count: number
  tags: string[]
  created_at: DateTime
}

export interface DatasetDetail {
  summary: DatasetSummary
  dataset: Dataset
}

export interface ValidationIssue {
  /** JSON-pointer-like location, e.g. /cases/3/expected/value */
  path: string
  message: string
  severity: 'error' | 'warning'
}

export interface DatasetValidation {
  valid: boolean
  issues: ValidationIssue[]
  content_hash: string | null
  case_count: number
}

// --- api/schemas.py: evaluations ---------------------------------------------------------

export interface DatasetRef {
  name: string
  version: string
}

export interface EvaluationCreate extends AgentSelector {
  dataset: DatasetRef
  label?: string
  thresholds?: GateThresholds | null
}

export interface SuiteListItem {
  suite_id: string
  label: string
  status: SuiteStatus
  dataset_name: string
  dataset_version: string
  dataset_hash: string
  agent_ref: string
  config_hash: string
  provider: string
  model: string
  deterministic: boolean
  started_at: DateTime
  finished_at: DateTime | null
  total: number
  passed: number
  pass_rate: number
  gate_passed: boolean | null
}

// --- api/schemas.py: experiments ---------------------------------------------------------

/** Either run both sides now (`dataset` + `a` + `b`) or compare two finished suites. */
export interface ExperimentCreate {
  name: string
  dataset?: DatasetRef | null
  a?: AgentSelector | null
  b?: AgentSelector | null
  suite_a?: string | null
  suite_b?: string | null
}

export interface ExperimentListItem {
  experiment_id: string
  name: string
  created_at: DateTime
  dataset_name: string
  dataset_version: string
  a_ref: string
  b_ref: string
  a_pass_rate: number
  b_pass_rate: number
  regressions: number
  fixes: number
  confounded: boolean
  deterministic: boolean
}

// --- api/schemas.py: failure analysis ----------------------------------------------------

export interface ResilienceRow {
  agent_ref: string
  fault_kind: string
  cases: number
  passed: number
  recovered: number
  degraded: number
  failed: number
}

export interface FailureBucket {
  check: string
  reason: string
  fault_kind: string | null
  run_status: string
  count: number
  example_run_ids: string[]
}

export interface InjectionSummary {
  agent_ref: string
  cases: number
  unauthorized_attempts: number
  unauthorized_executions: number
  leaked_cases: number
  deterministic: boolean
}

export interface FailureAnalysis {
  suites_considered: string[]
  resilience: ResilienceRow[]
  buckets: FailureBucket[]
  injection: InjectionSummary[]
}

// --- api/schemas.py: overview ------------------------------------------------------------

export interface PassRatePoint {
  suite_id: string
  finished_at: DateTime
  pass_rate: number
  dataset: string
  gate_passed: boolean | null
}

export interface PassRateSeries {
  agent_ref: string
  points: PassRatePoint[]
}

export interface Overview {
  counts: Record<string, number>
  recent_runs: RunSummary[]
  recent_suites: SuiteListItem[]
  pass_rate_series: PassRateSeries[]
  latest_gate: GateResult | null
  providers: ProviderStatus[]
}

// --- api/schemas.py: settings and data ---------------------------------------------------

export interface TableInventory {
  name: string
  rows: number
  description: string
}

export interface StorageInventory {
  backend: 'sqlite' | 'postgresql'
  location: string
  size_bytes: number | null
  tables: TableInventory[]
}

export interface SettingsView {
  version: string
  effective: Record<string, JsonValue>
  storage: StorageInventory
  egress: string[]
  privacy: string[]
}

export const PURGE_SCOPES = ['all', 'runs', 'evaluations', 'experiments', 'datasets'] as const
export type PurgeScope = (typeof PURGE_SCOPES)[number]

export interface PurgeRequest {
  confirm: 'PURGE'
  scope?: PurgeScope
  before?: DateTime | null
}

export interface PurgeResult {
  deleted: Record<string, number>
  size_bytes_before: number | null
  size_bytes_after: number | null
}

// --- GET /api/health (not a schemas.py model) --------------------------------------------

export interface Health {
  status: string
  version: string
}
