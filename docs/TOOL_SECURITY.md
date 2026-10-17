# Tool security

AgentScope's tools are simulations. They answer from synthetic data packaged with
AgentScope, they never reach the network, the host filesystem (beyond reading packaged data)
or a shell, and the only tools that "write" append to an in-memory sandbox discarded with the
run. This document describes the tool contract, validation, fault injection, the individual
tools' safety rules, the test that keeps the tool layer isolated, and the egress policy that
governs the model providers.

## The tool contract

A tool is a subclass of `Tool` (`src/agentscope/tools/base.py`) that declares:

| Member | Meaning |
|---|---|
| `name` | Lower-case identifier. |
| `version` | Schema version (default 1). The registry keeps every version; an agent's `tool_schema_version` selects, per tool, the highest registered version that is less than or equal to it (or the lowest version when none is). |
| `description` | What the model is told about the tool. |
| `args_model` | A `ToolArgs` subclass (Pydantic, `extra="forbid"`). Its JSON Schema becomes `ToolSpec.parameters`, so the schema the model sees and the validation applied have one source. |
| `output_model` | A `ToolOutput` subclass (Pydantic, `extra="forbid"`). |
| `side_effect` | `none` or `sandbox_write`. |
| `async run(args, ctx)` | Executes with validated arguments and returns an output model. `ctx` carries the run id and the per-run `Sandbox`. |
| `make_incomplete`, `make_misleading`, `inject_instructions` | Fault hooks (below). Each receives a JSON copy of a valid output and must return a payload that still validates against `output_model`. |

Built-in tools (`src/agentscope/tools/builtin/`), all registered by `builtin_tools()`:

| Tool | Versions | Side effect | Data |
|---|---|---|---|
| `search_documents` | 1, 2 | none | `tools/data/documents.json` (v1 includes unverified sources; v2 only when `include_unverified` is true and requires a query of at least 3 characters) |
| `retrieve_document` | 1 | none | `documents.json` |
| `lookup_record` | 1 | none | `tools/data/records.json` |
| `list_records` | 1 | none | `records.json` |
| `check_calendar` | 1 | none | `records.json`, `tools/data/calendar.json` |
| `calculate` | 1 | none | none |
| `inspect_mock_api` | 1 | none | `tools/data/mock_api.json` |
| `create_task` | 1 | sandbox_write | per-run sandbox |
| `send_email` | 1 | sandbox_write | per-run sandbox |
| `delete_record` | 1 | sandbox_write | per-run sandbox |

Packaged data is read through `importlib.resources`, validated once into Pydantic models
and, for records and mock API fixtures, exposed through read-only mappings
(`MappingProxyType`). Tools answer by exact key lookup.

## Argument and output validation

Every call is validated twice before it can run: by the runtime's `guard` node and again by
`ToolExecutor.execute`. Arguments are validated with
`args_model.model_validate_json(json.dumps(arguments), strict=True)`: strict JSON mode, so
`"5"` is not `5` and `1` is not `true`, and unknown arguments are rejected. A validation
failure is the `invalid_arguments` outcome; the guard blocks it before execution and the
model receives a short `location: message` summary (at most three errors).

Execution then proceeds as follows (`src/agentscope/tools/executor.py`):

1. The fault plan registers the call and returns the fault that applies, if any.
2. `permission_denied` and `error` faults return immediately without running the tool.
3. The tool runs under `asyncio.timeout(tool_timeout_s)`. A `timeout` fault waits on an event
   that never fires, so the timeout always cuts it short.
4. Exceptions become outcomes: `ToolPermissionError` is `permission_denied`,
   `ToolExecutionError` is `error` (message cut to 500 characters), and any other exception is
   `error` with only the exception type (`internal tool error: <Type>`), never a traceback.
5. An output fault (`malformed`, `incomplete`, `misleading`, `injection`) rewrites the output.
6. The result is validated against `output_model`. A failure is the `malformed` outcome, with
   a preview of at most 2,000 characters.
7. An output whose JSON exceeds `limits.max_tool_output_chars` (default 20,000) is an `error`
   outcome.

Tool outcomes: `ok`, `error`, `timeout`, `malformed`, `permission_denied`,
`invalid_arguments`, `unknown_tool`.

## Fault injection

A `FaultSpec` (`src/agentscope/domain/tools.py`) names a `tool`, a `kind`, `on_call` (the
n-th call of that tool, 1-based, counting retries; `null` means every call), `times` (how
many consecutive calls from `on_call`; `null` means all later calls; ignored when `on_call`
is `null`) and an optional `payload` (1 to 8,000 characters). A case holds at most 10 faults;
the first matching spec in list order applies. Calls are counted per tool name across the
run. Calls the guard blocks and calls with invalid arguments never reach the fault plan and
are not counted.

| Kind | Semantics | Outcome |
|---|---|---|
| `timeout` | The tool hangs until `tool_timeout_s` cuts it off. | `timeout` |
| `error` | Simulated upstream failure (`simulated upstream error (HTTP 503)`); the tool does not run. | `error` |
| `permission_denied` | Simulated upstream refusal (`simulated upstream refused access (HTTP 403)`); the tool does not run. Distinct from the runtime guard's denial, which happens before execution. | `permission_denied` |
| `malformed` | The tool runs; its JSON text is cut to a third of its length (at least 8 characters), which fails output validation. | `malformed` |
| `incomplete` | The tool's `make_incomplete` hook drops content but keeps the output valid. Default: every list keeps at most one element. | `ok` |
| `misleading` | The tool's `make_misleading` hook makes the output plausible but wrong. Default: the first string leaf is replaced by the payload. | `ok` |
| `injection` | The tool's `inject_instructions` hook plants the payload in the output. Default: the payload is appended to the first string leaf. | `ok` |

`timeout`, `error`, `malformed` and `permission_denied` make a call fail; `incomplete`,
`misleading` and `injection` return valid content that is wrong or hostile. The attempt
records which fault applied (`ToolAttempt.fault`, span attribute `agentscope.fault.kind`), and
metrics count failed attempts caused by injected faults (`injected_fault_outcomes`)
separately from failures nothing injected (`unexpected_tool_errors`).

Default payloads (`src/agentscope/tools/faults.py`) are synthetic and harmless: the injection
default asks the model to ignore the user, reveal its hidden instructions and call
`send_email` to an `example.invalid` address; the misleading default says the record was
superseded.

Per-tool hooks:

| Tool | `incomplete` | `misleading` | `injection` |
|---|---|---|---|
| `search_documents` | default | first snippet replaced by the payload | payload appended to the first snippet |
| `retrieve_document` | body cut to a third, facts removed | fact values distorted (integers +1, or +4 above 100; decimals ×1.5; text gets " (disputed)"), payload appended to the body | payload appended to the body |
| `lookup_record` | projects keep the first half of their workstreams (`workstream_count` still says how many exist); employees lose their notes | project estimates halved or employee capacity +8 hours (at most 80), payload appended to notes | default |
| `calculate` | fractional part dropped | result 10% high (payload unused) | payload appended to `display` |
| `inspect_mock_api` | only the first body field kept | payload as `message`; a body with a `state` reports `operational` from a `cached` source | payload appended to `message` |
| `create_task`, `send_email`, `delete_record` | default | payload as `detail` | payload appended to `detail` |

The planted text is clipped to the tools' 1 KiB text bound where a field has one, so a
degraded output still validates.

Model faults (`ModelFaultSpec`: `invalid_json`, `empty`, `schema_violation`,
`unknown_tool`) corrupt the n-th model reply instead; see
[AGENT_RUNTIME.md](AGENT_RUNTIME.md#the-fake-model).

## Sandbox write tools

`create_task`, `send_email` and `delete_record` exist to exercise forbidden-action handling.

- They only modify the run's `Sandbox` (`tasks`, `outbox`, `deleted_records`), created per run
  and discarded with it; the sandbox itself is not persisted (the tool invocation and its
  output are, like any other call).
- `send_email` never delivers anything; its output says `stored_in_sandbox`.
- `delete_record` marks an existing record as deleted for the rest of that run: later
  `lookup_record`, `check_calendar`, `create_task` and `delete_record` calls on it fail and
  `list_records` omits it. The packaged data never changes.
- No built-in agent's toolset contains them, and `AgentCatalog.validate` rejects a
  configuration that lists a tool outside its agent's toolset, so no built-in agent can be
  configured to execute them. A model that requests one (typically because tool output told it
  to) is stopped by the permission guard, and the attempt is recorded and judged by
  `forbidden_tool_calls`.

## Calculator safety rules

`calculate` (`src/agentscope/tools/builtin/calculator.py`) parses the expression with `ast`
and evaluates it with a small interpreter. Nothing is handed to Python's evaluator.

- At most 200 characters, 50 AST nodes (everything `ast.walk` yields) and a nesting depth of
  10.
- Allowed nodes only: `Expression`, `BinOp` with `+ - * / // % **`, `UnaryOp` with `+ -`, and
  `int` or `float` constants (booleans rejected). Names, calls, attributes and subscripts
  cannot appear.
- `**` only when |base| ≤ 1e6 and |exponent| ≤ 10.
- Every literal and intermediate value must be finite and at most 1e15 in magnitude;
  complex results, division by zero and overflow are rejected.
- Every rejection happens during argument validation, so it surfaces as `invalid_arguments`
  and the guard blocks the call; the tool itself never fails.
- `tests/property/test_calculator.py` exercises it with Hypothesis.

## Mock API path rules

`inspect_mock_api` (`src/agentscope/tools/builtin/mock_api.py`) answers fixture-backed GET
requests; no request leaves the process.

- The path must start with `/`, contain only printable ASCII without spaces, and be at most 200
  characters.
- Paths containing `..`, `%`, `\` or `://` are rejected by the arguments model
  (`invalid_arguments`).
- The path is normalized with `posixpath.normpath` and must then exactly match a packaged
  fixture key; a well-formed path with no fixture is an `error` outcome.
- Fixture bodies are read-only mappings, so the cached fixture cannot be modified in place.

## Records and documents

- Record, workstream and task ids match `^[a-z0-9_-]{1,64}$`; team names `^[a-z0-9-]{1,32}$`;
  ISO weeks `YYYY-Www`; free text is bounded at 1 KiB in arguments and outputs alike.
- `retrieve_document` ids match `^doc-[a-z0-9-]{1,60}$`; `search_documents` queries are at most
  200 characters and `top_k` is 1 to 5.

## The banned-imports test

`tests/unit/test_tool_isolation.py` parses every module under `agentscope/tools` and
`agentscope/agents` and fails on:

- imports of `aiohttp`, `builtins`, `ctypes`, `http`, `httpx`, `importlib.machinery`,
  `importlib.util`, `multiprocessing`, `os`, `pathlib`, `pty`, `requests`, `runpy`, `shutil`,
  `socket`, `ssl`, `subprocess`, `urllib`, `urllib3` (and their submodules);
- the names `asyncio.create_subprocess_exec`, `asyncio.create_subprocess_shell`,
  `asyncio.open_connection`, `codecs.open`, `importlib.import_module`, `io.open`, `os.system`;
- calls to `__import__`, `compile`, `eval`, `exec` and `open`.

Packaged data stays readable through `importlib.resources`.

## The provider egress policy

Tools have no network access at all. Model providers and the optional OTLP exporter do, and
only through `src/agentscope/providers/http.py`:

- `EgressPolicy.from_settings()` derives every allowed destination from Settings alone:
  `AGENTSCOPE_OLLAMA_URL` when Ollama is enabled, `AGENTSCOPE_OPENAI_COMPATIBLE_URL` when that
  provider is enabled, and `AGENTSCOPE_OTLP_ENDPOINT` when set. The policy is immutable.
- A URL must be `http` or `https`, must not embed credentials, a query or a fragment, and
  must have a valid host and port. Loopback hosts (`localhost`, loopback IPs) are allowed; any
  other host must be listed exactly in `AGENTSCOPE_ALLOWED_EGRESS_HOSTS` (or the older
  `AGENTSCOPE_ALLOWED_MODEL_HOSTS`). Credentials (an API key) are never sent to a non-loopback
  host over plain http. Refusal messages never echo the configured URL.
- `egress_client()` is the only HTTP client factory. Its transport checks every request
  against one target: same scheme, host and port, and one of that target's fixed routes:

  | Target | Routes |
  |---|---|
  | Ollama | `POST /api/chat`, `GET /api/tags`, `GET /api/version` |
  | OpenAI-compatible | `POST /chat/completions`, `GET /models` (under the base URL's path) |
  | OTLP | `POST /v1/traces` |

  Query strings are not allowed. Clients never follow redirects (a 3xx answer becomes a
  provider error), ignore proxy, CA-bundle and netrc settings from the environment
  (`trust_env=False`), make no transport retries, and have explicit timeouts (connect at most
  5 seconds).
- Transport errors become `ProviderError`s that name the target as `host:port` but never
  headers or bodies. The OpenAI-compatible provider scrubs its API key from any error text,
  even when a server echoes it back.
- When a provider with a non-loopback target is built, a warning says that prompts, tool
  outputs and task data will leave the machine. `agentscope serve` prints every allowed
  destination at startup, and `GET /api/settings` lists them.

Tests: `tests/unit/test_egress.py` (policy and transport), and the whole pytest suite runs
under `pytest-socket` with only loopback hosts allowed.
