"""Span attribute names.

`gen_ai.*` names follow the OpenTelemetry Generative AI semantic conventions (development
status), as published in opentelemetry/semantic-conventions v1.37+ (docs/gen-ai/). They are
spelled out here rather than imported from `opentelemetry.semconv._incubating`, which is
not a stable import path. `agentscope.*` names are this project's own.
"""

# --- OpenTelemetry GenAI semantic conventions ---------------------------------------
GEN_AI_OPERATION_NAME = "gen_ai.operation.name"  # chat | execute_tool | invoke_agent
GEN_AI_PROVIDER_NAME = "gen_ai.provider.name"
GEN_AI_AGENT_NAME = "gen_ai.agent.name"
GEN_AI_REQUEST_MODEL = "gen_ai.request.model"
GEN_AI_REQUEST_TEMPERATURE = "gen_ai.request.temperature"
GEN_AI_REQUEST_SEED = "gen_ai.request.seed"
GEN_AI_REQUEST_MAX_TOKENS = "gen_ai.request.max_tokens"
GEN_AI_RESPONSE_MODEL = "gen_ai.response.model"
GEN_AI_RESPONSE_FINISH_REASONS = "gen_ai.response.finish_reasons"
GEN_AI_USAGE_INPUT_TOKENS = "gen_ai.usage.input_tokens"
GEN_AI_USAGE_OUTPUT_TOKENS = "gen_ai.usage.output_tokens"
GEN_AI_TOOL_NAME = "gen_ai.tool.name"
GEN_AI_TOOL_CALL_ID = "gen_ai.tool.call.id"

# --- AgentScope ----------------------------------------------------------------------
AS_SPAN_KIND = "agentscope.span.kind"
AS_INPUT = "agentscope.input"
AS_OUTPUT = "agentscope.output"
AS_PAYLOAD_TRUNCATED = "agentscope.payload.truncated"
AS_CANCELLED = "agentscope.cancelled"
AS_RUN_ID = "agentscope.run_id"
AS_CASE_ID = "agentscope.case_id"
AS_AGENT_VERSION = "agentscope.agent.version"
AS_CONFIG_HASH = "agentscope.config_hash"
AS_EFFECTIVE_CONFIG_HASH = "agentscope.effective_config_hash"
AS_STEP = "agentscope.step"
AS_NODE = "agentscope.node"
AS_ROUTE = "agentscope.route"
AS_DECISION = "agentscope.decision"
AS_DECISION_EXTRA_KEYS = "agentscope.decision.extra_keys"
AS_ATTEMPT = "agentscope.tool.attempt"
AS_TOOL_OUTCOME = "agentscope.tool.outcome"
AS_FAULT_KIND = "agentscope.fault.kind"
AS_MODEL_FAULT = "agentscope.model_fault.kind"
AS_USAGE_REPORTED = "agentscope.usage.reported"
AS_GUARDRAIL_ALLOWED = "agentscope.guardrail.allowed"
AS_GUARDRAIL_FINDINGS = "agentscope.guardrail.findings"
AS_OBSERVATION_DELIMITED = "agentscope.observation.delimited"
AS_OUTPUT_VALID = "agentscope.output.valid"
AS_RUN_STATUS = "agentscope.run.status"
AS_RUN_STEPS = "agentscope.run.steps"
AS_FAULTS = "agentscope.faults"
