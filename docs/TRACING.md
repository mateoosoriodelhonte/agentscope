# Tracing

Every run is recorded as one OpenTelemetry trace. Spans are created with the OpenTelemetry
SDK, redacted as they are written, collected per run, converted to `SpanRecord`s
(`src/agentscope/domain/traces.py`) and stored with the run. Export to an OTLP collector is
optional and off by default. Code: `src/agentscope/tracing/`.

## Span model

| Span name | `agentscope.span.kind` | OTel kind | Parent | Created by |
|---|---|---|---|---|
| `invoke_agent {agent}` | `run` | INTERNAL | none (root of a new trace) | the runner |
| `node {name}` (`plan`, `guard`, `act`, `observe`, `validate`, `finish`) | `node` | INTERNAL | the run span | every graph node |
| `chat {model}` | `llm` | CLIENT | the `plan` node span | each model call |
| `execute_tool {tool}` | `tool` | INTERNAL | the `act` node span | each tool attempt (retries get their own span) |
| `guardrail tool_policy` | `guardrail` | INTERNAL | the `guard` node span | a call the guard blocked |
| `guardrail content_scan` | `guardrail` | INTERNAL | the `observe` node span | the heuristic scanner found instruction-like content |

`SpanRecord` fields: `trace_id` (32 hex), `span_id` (16 hex), `parent_span_id`, `name`,
`kind`, `status` (`unset`, `ok`, `error`), `status_message`, `start_time`, `end_time`,
`duration_ms`, `attributes`, `input`, `output`, `events`. Spans are ordered by start time.

- Status: nodes, successful model calls and successful tool attempts end `ok`; a failed tool
  attempt ends `error` with its error text; a failed model call ends `error` with the provider
  message; the run span is `ok` only when the run completed, and otherwise `error` with the
  run's error text.
- Every failed span also gets the attribute `error.type`, a short code that never carries
  data: the tool outcome (`timeout`, `error`, `malformed`, ...), the provider failure
  (`provider_error`, `provider_timeout`), the run's error code (`max_steps`, `run_timeout`,
  `invalid_decision`, ...), `cancelled`, or an exception type.
- Exceptions are never recorded as span events (`record_exception=False`): the span status
  carries only the exception type, because messages and tracebacks can carry data.
- When the run deadline cancels a span, it gets `agentscope.cancelled=true`, the status
  `cancelled: run deadline` and `error.type=cancelled`.
- Span names are redacted and cut to 256 characters like any other written value, because an
  `execute_tool {tool}` name can carry a tool name the model chose.
- Events: the `act` span gets a `tool_retry` event (`tool`, `after_outcome`,
  `retry.source=runtime`) before each runtime retry.
- State transitions are not span events: they are stored on the run (`TraceRecord.transitions`)
  with their sequence number, step, source, target and reason.

## Attribute names

From `src/agentscope/tracing/semconv.py`. `gen_ai.*` attributes:

| Attribute | Spans | Value |
|---|---|---|
| `gen_ai.operation.name` | run, chat, tool | `invoke_agent`, `chat`, `execute_tool` |
| `gen_ai.provider.name` | run, chat | `agentscope.fake` for the fake provider, otherwise `ollama` or `openai_compatible` |
| `gen_ai.agent.name` | run | agent name |
| `gen_ai.request.model` | run, chat | model name from the configuration |
| `gen_ai.request.temperature`, `gen_ai.request.seed`, `gen_ai.request.max_tokens` | chat | from `ModelConfig` (seed omitted when null) |
| `gen_ai.response.model` | chat | as reported by the provider |
| `gen_ai.response.finish_reasons` | chat | a string array holding the provider's finish reason (`["stop"]` for the fake provider); omitted when none was reported |
| `gen_ai.usage.input_tokens`, `gen_ai.usage.output_tokens` | chat | only when the provider reported them |
| `gen_ai.tool.name` | plan (tool call decisions), tool, guardrail | tool name |
| `gen_ai.tool.call.id` | tool | `call_001`, `call_002`, ... per run |

General OpenTelemetry attribute:

| Attribute | Spans | Value |
|---|---|---|
| `error.type` | any span that ended `error` | the class of error (see above), never a message |

`agentscope.*` attributes:

| Attribute | Spans | Meaning |
|---|---|---|
| `agentscope.span.kind` | all | `run`, `node`, `llm`, `tool`, `guardrail` (removed from `attributes` when converted; it becomes `SpanRecord.kind`) |
| `agentscope.input`, `agentscope.output` | all that carry payloads | redacted JSON payloads (become `SpanRecord.input` and `output`) |
| `agentscope.payload.truncated` | any | true when a payload was shortened |
| `agentscope.cancelled` | any | the run deadline cancelled this span |
| `agentscope.run_id`, `agentscope.case_id` | run | run id; test case id when evaluated |
| `agentscope.agent.version` | run | agent version |
| `agentscope.config_hash`, `agentscope.effective_config_hash` | run | configuration hashes |
| `agentscope.faults` | run | number of tool and model faults injected |
| `agentscope.run.status`, `agentscope.run.steps` | run (`run.status` also on `finish`) | terminal status, step count |
| `agentscope.step`, `agentscope.node`, `agentscope.route` | node (`step` also on chat) | step number, node name, chosen route |
| `agentscope.decision` | plan | `tool_call` or `final` |
| `agentscope.decision.extra_keys` | plan | unknown keys dropped from the decision |
| `agentscope.usage.reported` | chat | whether the provider reported usage |
| `agentscope.model_fault.kind` | chat | the injected model fault, if any |
| `agentscope.tool.attempt` | tool | attempt number (1-based) |
| `agentscope.tool.outcome` | tool | `ok`, `error`, `timeout`, `malformed`, `permission_denied`, ... |
| `agentscope.fault.kind` | tool | the injected tool fault delivered on this attempt, if any |
| `agentscope.fault.undelivered` | tool | an output fault that was due on this attempt but had nothing to act on (the tool itself failed, or the fault would not have changed the output); it stays owed. Not set when an injected `timeout`, `error` or `permission_denied` fault took the attempt |
| `agentscope.guardrail.allowed` | `guardrail tool_policy` | always false (only blocked calls get this span) |
| `agentscope.guardrail.findings` | `guardrail content_scan` | number of signatures found |
| `agentscope.observation.delimited` | observe | whether the result was delivered as untrusted data |
| `agentscope.output.valid` | validate | whether the final output matched the schema |

An attribute whose value is unknown is omitted, never written as a placeholder.

## GenAI semantic conventions

The `gen_ai.*` names follow the OpenTelemetry Generative AI semantic conventions (development
status) as published in `opentelemetry/semantic-conventions` v1.37 and later. They are
spelled out in `semconv.py` rather than imported from `opentelemetry.semconv._incubating`,
which is not a stable import path. Departures to be aware of:

- Payloads are written to `agentscope.input` and `agentscope.output`, not to the conventions'
  opt-in content attributes (`gen_ai.system_instructions`, `gen_ai.input.messages`,
  `gen_ai.output.messages`, `gen_ai.tool.call.arguments`, `gen_ai.tool.call.result`).
  Those names are listed in `PAYLOAD_ATTRIBUTES` only so the export filter would drop them if
  they ever appeared.
- `gen_ai.response.finish_reasons` holds at most one entry, because providers report one
  finish reason per call.
- The fake provider is identified as `agentscope.fake`, which is not a registered provider
  name.

## Redaction at write time

`Redactor` (`src/agentscope/tracing/redaction.py`) applies these rules:

1. **Configured secret values**: every `SecretStr` in Settings (API keys, the API token, OTLP
   header values, the database URL) and the database password parsed from the DSN, replaced
   with `[REDACTED:configured_secret]`. A value is matched only if it is distinctive enough
   not to be an ordinary word: values of 16 or more characters are matched anywhere; values
   of 8 to 15 characters must mix at least two character classes (lower case, upper case,
   digits, other) and are matched as whole tokens only (not when an ASCII letter or digit
   adjoins them); shorter values, and values with fewer than 4 distinct characters, are never
   matched by value. A database password such as `agentscope` therefore no longer rewrites
   the provider label `agentscope.fake` or dataset text. AgentScope logs one warning with the
   number of configured values too weak to match (never the values); they are still redacted
   inside URLs and `name=value` text by the rules below.
2. **Sensitive key names**: the value under a secret-like key is replaced wholesale with
   `[REDACTED]`. Key names are split into words at camelCase boundaries and at `_`, `-`, `.`
   and spaces. A key is secret-like when its last word ends in `token` (`githubToken`,
   `access_token`, `refreshtoken`), when any word is `secret(s)`, `password(s)`, `passwd`,
   `passphrase`, `credential(s)`, `authorization`, `cookie(s)` or `bearer`, or when a word or
   two adjacent words join to `apikey`, `clientsecret`, `privatekey`, `secretkey`,
   `accesskey`, `signingkey`, `encryptionkey`, `masterkey`, `sessionid`, `sessiontoken` or
   `setcookie`. So `SECRET_KEY`, `AWS_SECRET_ACCESS_KEY`, `db_password_plain` and `x-api-key`
   are caught, while `max_tokens`, `input_tokens`, `token_count` and `secretary` are not.
   Names listed in `AGENTSCOPE_REDACT_KEYS` (exact, case-insensitive) are redacted too.
3. **Secret-shaped values** anywhere in text, replaced with `[REDACTED:<kind>]`: PEM private
   keys (`private_key`), `sk-ant-…` (`anthropic_key`), `sk-…`/`sk-proj-…` (`openai_key`),
   GitHub tokens (`github_token`), AWS access key ids (`aws_access_key`), Slack tokens
   (`slack_token`), JWTs (`jwt`), `Bearer …` (`bearer`), and the password in
   `scheme://user:password@host` (`url_password`; the rest of the URL stays readable).
4. **Assignments** in text: in `name=value`, `name: value` and `"name": "value"`, the value
   (4 or more characters) is replaced with `[REDACTED:assignment]` when the name is
   secret-like by rule 2, and the name is kept: `password=hunter22` becomes
   `password=[REDACTED:assignment]`. Prose such as `token: field` is redacted as well.
5. **Object keys** are text too: a key that contains a secret is renamed to its redacted
   form (for example `[REDACTED:openai_key]`), numbered `#2`, `#3` when that would collide
   with another key.
6. **Unstorable characters**: NUL and unpaired UTF-16 surrogates become U+FFFD (a split
   surrogate pair is joined back), so redacted text can always be stored and served.

Redaction is idempotent, stops at a nesting depth of 32 (`[TRUNCATED:depth]`), and every
pattern runs in linear time, because datasets and model output are untrusted. It is applied:

- when anything is written to a span: span names (cut to 256 characters), payloads
  (redacted, then serialized), string attributes and string list items (cut to 4,096
  characters), event attributes (2,048) and status messages (500);
- to the task before it is sent to the model, and to transition reasons, the stored task,
  error text and fault payloads in the runner;
- by the repository to every model before it is saved (traces, cases, verdicts, datasets,
  suites, comparisons), and by the services layer to everything returned to the CLI or the
  API. A redaction marker can be longer than the secret it replaces; a string field pushed
  past its length limit that way is cut back to the limit and ends with `…[truncated]`.

The final output and tool payloads stay unredacted in memory until the checks have run, so a
secret-shaped string a model leaked is still detected as a leak
(`tests/integration/test_unredacted_evaluation.py`). Decoded model output has NUL characters
and unpaired surrogates replaced already
([AGENT_RUNTIME.md](AGENT_RUNTIME.md#the-decision-protocol-and-parsing)).

Payload size: a payload is serialized as JSON with at most 65,536 characters. If it is
larger, string leaves longer than 8,192 characters are cut (with `… [truncated N chars]`);
if it is still too large, it is replaced by `{"_truncated": true, "_original_chars": N,
"preview": ...}`, where `N` is the length of the JSON after the leaves were cut and `preview`
holds its first 4,096 characters. Either way the span gets
`agentscope.payload.truncated=true`.

Redaction is pattern-based: a secret that matches no pattern, key name or configured value is
stored as written. See [SECURITY.md](../SECURITY.md#known-limitations).

## Token usage and cost

- Token counts are exactly what the provider reported (`TokenUsage.source =
  "provider_reported"`); nothing is estimated. A count that is not a non-negative integer
  counts as not reported. Ollama omits `prompt_eval_count` when a prompt is served from its
  cache, which shows up as a missing input count.
- On a chat span, `gen_ai.usage.*` attributes are omitted when nothing was reported (never
  written as 0), and `agentscope.usage.reported` says which case applies.
- A run's `usage` sums the calls that reported both counts and records `reported_calls` out of
  `total_calls`; it is `null` when no call reported. The fake provider reports nothing, so
  fake runs have no usage.
- Cost (`CostEstimate`) is computed only when `AGENTSCOPE_MODEL_RATES` has an entry for the
  run's model name and the run reported both token totals. It is labelled
  `estimate_from_configured_rates`, in USD, and `partial` when some calls reported nothing.
- A suite's `usage` sums only runs with complete usage and states `reporting_runs` out of
  `total_runs`.
- The dashboard shows "not reported" instead of a number when the provider reported no
  tokens (whatever the rates), and "not configured" for a cost when tokens were reported but
  no rate exists for the model. It labels a cost as an estimate ("est.", "(partial)").

## Opt-in OTLP export

Nothing is exported unless all of these hold:

1. The `otlp` extra is installed (`uv sync --extra otlp`, or `--all-extras`). Without it a
   warning is logged and runs continue unexported.
2. `AGENTSCOPE_OTLP_ENDPOINT` is set. It may be the collector's base URL or its full
   `/v1/traces` URL; only `POST /v1/traces` is ever sent.
3. The egress policy accepts the endpoint: loopback, or a host listed in
   `AGENTSCOPE_ALLOWED_EGRESS_HOSTS`. Otherwise export is disabled with a warning.
4. If `AGENTSCOPE_OTLP_HEADERS` (a JSON object, values treated as secrets) is set, the endpoint
   is loopback or https; otherwise export is disabled with a warning rather than sending the
   headers in clear.

What is sent:

- Spans are already redacted (see above), whatever the settings.
- By default (`AGENTSCOPE_OTLP_EXPORT_CONTENT=false`), `ContentFilteringExporter` rebuilds each
  span without payload attributes on the span and on its events: `agentscope.input`,
  `agentscope.output`, the GenAI content attributes listed above, `exception.message` and
  `exception.stacktrace`. It also reduces the status description of every failed span to its
  `error.type` (or `error`), because tool and provider error messages can echo arguments
  taken from the task ("no project record with id ..."). What remains is span structure:
  span names (redacted), statuses, error types, the GenAI metadata and the `agentscope.*`
  attributes other than the payloads.
- `AGENTSCOPE_OTLP_EXPORT_CONTENT=true` exports payloads (task, prompts, tool arguments and
  results) and the full status descriptions (at most 500 characters) too, still redacted.
- The local trace always keeps the payloads and the full, redacted status descriptions.
- The resource carries `service.name=agentscope` and `service.version`.

How it is sent: a `BatchSpanProcessor` on a worker thread (queue 2,048, batches of 512, every
5 seconds), an OTLP/HTTP protobuf exporter with a 5-second timeout and no compression, over a
`requests` session that ignores proxy, CA-bundle and netrc settings from the environment and
refuses redirects. `OTEL_*` variables are removed before the exporter is built, because the
library would otherwise merge `OTEL_EXPORTER_OTLP_HEADERS` into the explicit headers. Export
failures are counted, the first one is logged, and a run's outcome and local trace never
depend on the collector. When the endpoint is not loopback, a warning says span data leaves
the machine. The exporting pipeline is installed once per process, when `build_services`
runs with an endpoint configured.

## Viewing traces

**Dashboard.** *Runs* lists stored runs with filters (status, agent, has faults, has blocked
calls, verdict). A run opens the trace explorer (`/runs/:runId`):

- a header with status, agent, provider and model, steps, duration, tool calls (executed and
  blocked), tokens and cost (or "not reported" / "not configured"), injected faults, and the
  simulated-model badge for fake runs;
- a transition strip with one segment per step, where only transitions that leave the normal
  loop (denials, retries, repairs, abnormal ends) get reason chips; it doubles as a minimap;
- a span tree grouped by step, with outcome and duration columns; duration bars switch between
  relative and timeline mode (`t`);
- detail tabs that depend on the span: Decision and Messages for plan and chat spans (exactly
  what the model saw), Verdict for guard and `guardrail tool_policy` spans, Findings for
  `guardrail content_scan` spans, Call, Result and Attempts for tool spans, Observation,
  Validation, Outcome, and a Raw tab with every attribute;
- a verdict panel (failures first) when the run was evaluated: activating a check sets
  `?check=`, selects and outlines its evidence spans (`n` / `N` cycle through them), and shows
  an expected-versus-executed tool sequence for evidence about something that never happened.

Keyboard shortcuts are listed with `?` and only act while the trace region has focus.
*Compare* opens a step-aligned diff of two runs (`/compare/runs?a=&b=`).

**CLI.**

```sh
agentscope traces list [--limit N] [--agent NAME] [--json]
agentscope traces show RUN_ID [--json]
```

`traces show` prints the summary (with the simulated-model notice for fake-provider runs) and
the dashboard link for the run; `--json` prints the full stored trace and verdict plus a
`simulated` flag. `agentscope eval` prints a dashboard link with `?check=<first failed
check>` for every failed case of a saved suite. The Runs page shows each run's short id with
a button that copies the full id, which *Compare runs* needs.

**Exports.** `GET /api/runs/{id}/export` and `GET /api/evaluations/{id}/export` return JSON
attachments; `agentscope traces show --json`, `agentscope eval --json` and `--output FILE`
write the same records from the CLI. Everything exported is the stored, redacted record.
