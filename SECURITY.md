# Security

## Reporting a vulnerability

Please report vulnerabilities privately through GitHub security advisories:
<https://github.com/mateoosoriodelhonte/agentscope/security/advisories/new>. Do not open a
public issue, pull request or discussion for a suspected vulnerability. Include the
AgentScope version (`agentscope version`), how to reproduce the problem, and what an attacker
gains. Please give the maintainers time to release a fix before disclosing it publicly.

## Threat model

AgentScope is a **local, single-user tool**. The person running it, their operating-system
account and the AgentScope code they installed are trusted. Everything below is not:

| Untrusted input | Why it matters | Main defenses |
|---|---|---|
| Imported datasets | Shared between people, checked into repositories, uploaded through the API. A crafted file could exhaust resources, smuggle a value past a reviewer (duplicate keys), or plant secrets. | Hardened loader, strict validation, lint, redaction refusal on import, body limits. |
| Tool outputs | Simulated, but deliberately carry injected instructions, malformed and misleading content. | Strict output validation, untrusted-content delimiting, the permission guard, sandbox-only write tools. |
| Model outputs | A model can request tools it may not use, leak its instructions, or emit hostile strings. | Decision parsing and strict validation, the permission guard, an evaluator independent of the runtime, safe rendering in the dashboard and CLI. |
| Web pages in the user's browser | Any site can make the browser send requests to `127.0.0.1` (cross-site request forgery) or rebind its own hostname to `127.0.0.1` (DNS rebinding). | Host check, mutation guard, strict CSP and security headers. |
| Responses from configured remote servers | Model servers and collectors can answer with hostile content, redirects or echoed credentials. | Egress policy, no redirects, response validation, key scrubbing, printable-only error text. |
| Trace and dataset strings shown to the user | They can contain markup, terminal escape sequences or bidi overrides. | Text-only rendering in the dashboard, control-character stripping in the CLI. |

Out of scope: several users or tenants sharing one instance, an attacker who already runs code
as the user, and malicious changes to AgentScope or its dependencies (mitigated only by the
supply-chain measures below).

## Defenses

### Loopback binding and the Host check (DNS rebinding)

`agentscope serve` binds `127.0.0.1` by default and refuses a non-loopback bind unless remote
mode is on (exit 2). `LocalOnlyMiddleware` (`src/agentscope/api/security.py`) rejects any
request whose `Host` header, parsed IPv6-aware, is not `127.0.0.1`, `localhost` or `::1`
(plus explicit `--allowed-host` values; `*` is ignored) with `400 invalid host header`. A
rebinding attack reaches the server with the attacker's hostname in `Host`, so it is refused.

### Mutation guard (cross-site request forgery)

Every request other than `GET`, `HEAD` and `OPTIONS` must:

- use `Content-Type: application/json` (forms cannot);
- carry `X-AgentScope-Client: 1` (a custom header forces a CORS preflight, which the server
  never grants: it sends no CORS headers);
- if an `Origin` header is present, match the server's own origin (the request scheme and its
  already-checked `Host` header) or a value in `AGENTSCOPE_ALLOWED_ORIGINS` (default: the Vite
  dev server origins); `Origin: null` is rejected;
- not be a `cross-site` or `same-site` fetch according to `Sec-Fetch-Site` (only
  `same-origin` and `none` pass).

Failures answer 403. No `GET` route changes state; dataset import takes a JSON body, never
multipart; purge requires `{"confirm": "PURGE"}`.

### Strict CSP and security headers

Every response, error responses included, carries:

```
Content-Security-Policy: default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; font-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'
X-Content-Type-Options: nosniff
Referrer-Policy: no-referrer
X-Frame-Options: DENY
Cross-Origin-Opener-Policy: same-origin
Cross-Origin-Resource-Policy: same-origin
```

The dashboard contains no inline scripts (the theme bootstrap is the external
`/theme-init.js`) and the build never inlines assets as `data:` URIs. Exports are served as
`Content-Disposition: attachment`.

### Request body limits

The middleware counts received bytes, whatever `Content-Length` claims: 256 KiB per request,
and `AGENTSCOPE_MAX_REQUEST_BYTES` (default 5 MiB) for `/api/datasets` requests. Larger bodies
answer `413`.

### Errors without data

Unhandled exceptions answer `{"detail": "internal error"}` (500) and are logged as type,
method and path only. Request validation errors list locations without echoing the submitted
values. Runs record exception types, not messages or tracebacks; tool and provider errors are
cut to 500 characters and redacted.

### Redaction at write time

The task is redacted before it is sent to the model, so a secret pasted into a task never
reaches a provider. Span names, payloads, attributes, events and status messages are
redacted when written to a span; the repository redacts every model again before saving it;
everything returned to the CLI or the API is a redacted copy. Redaction covers:

- the exact values of configured secrets (every `SecretStr` setting and the database
  password), provided they are distinctive enough not to be ordinary words: 16 or more
  characters are matched anywhere, 8 to 15 characters with at least two character classes
  are matched as whole tokens, and weaker values are not matched by value (one warning gives
  their number, never the values);
- values under secret-like key names, matched by word (`api_key`, `AWS_SECRET_ACCESS_KEY`,
  `githubToken`, `session_id`, ...) or listed in `AGENTSCOPE_REDACT_KEYS`;
- secret-shaped strings (provider keys, GitHub, AWS and Slack tokens, JWTs, bearer
  credentials, PEM private keys) and the password in `scheme://user:password@host`;
- the value in `name=value`, `name: value` and `"name": "value"` text when the name is
  secret-like (the name is kept);
- object keys, which are renamed to their redacted form when they contain a secret.

NUL characters and unpaired surrogates become U+FFFD, and every pattern runs in linear time
on hostile input. Checks still judge the unredacted in-memory trace, so a leak is detected
even when the stored copy is redacted. Configured URLs shown by `GET /api/settings` have
everything up to the last `@` masked and their query and fragment dropped. Details:
[docs/TRACING.md](docs/TRACING.md#redaction-at-write-time).

### Egress policy and no telemetry

- `_bootstrap.py` runs before any third-party import: LangSmith/LangChain tracing is forced
  off, their API keys and endpoints are removed, and `OTEL_EXPORTER_OTLP_*`, `OTEL_SPAN_*`,
  `OTEL_ATTRIBUTE_*`, `OTEL_TRACES_EXPORTER` and `OTEL_SDK_DISABLED` are removed. Each run
  also passes `callbacks=[]` to LangGraph and disables LangSmith tracing for its duration.
- Every outbound destination comes from an immutable policy built from Settings: only the
  configured Ollama, OpenAI-compatible and OTLP targets, loopback unless a host is
  allowlisted, credentials and OTLP headers to non-loopback hosts only over https. Model
  provider requests go through one client factory whose transport checks each request
  against its target and fixed routes; the OTLP exporter sends only to the policy's
  `/v1/traces` URL. Neither follows redirects or reads proxy or netrc settings from the
  environment. Details: [docs/TOOL_SECURITY.md](docs/TOOL_SECURITY.md#the-provider-egress-policy).
- No `.env` file is read implicitly, so a project-local file cannot redirect a base URL while
  an API key comes from the shell.
- The pytest suite runs under `pytest-socket` with only loopback hosts allowed.

### No shell or HTTP tools

All ten tools are simulations over packaged data. `tests/unit/test_tool_isolation.py` fails if
any module under `agentscope/tools` or `agentscope/agents` imports networking, process,
filesystem or dynamic-import modules (`httpx`, `requests`, `socket`, `subprocess`, `os`,
`pathlib`, `ctypes`, `importlib.util`, ...) or calls `open`, `eval`, `exec`, `compile` or
`__import__`. The calculator evaluates an allowlisted AST with strict bounds; the mock API
answers only exact fixture paths after rejecting traversal, percent-encoding, backslashes and
absolute URLs ([docs/TOOL_SECURITY.md](docs/TOOL_SECURITY.md)).

### Sandbox-only write tools and the permission guard

`create_task`, `send_email` and `delete_record` only append to a per-run, in-memory sandbox
that is discarded after the run. No built-in agent's toolset includes them, so no
configuration of a built-in agent can permit them. The runtime guard executes a call only if
the tool exists, is in the run's effective permitted set (the agent's tools, narrowed by the
case), and receives arguments that validate strictly; anything else is recorded and never
executed. `tests/injection/` shows that a model that obeys every injected instruction
(`scripted-compliant`) executes no unauthorized tool.

### Untrusted content handling

- Prompts: with `isolate_untrusted`, tool results (including errors, fault payloads and the
  runtime's refusal messages) are HTML-escaped inside one `<tool_output>` block that content
  cannot close or forge; hardened prompts add an instruction-hierarchy clause.
- Dashboard: no `v-html` (ESLint `vue/no-v-html: error`), and a test fails on `innerHTML`,
  `outerHTML`, `insertAdjacentHTML` or `document.write(` in application sources; payloads are
  shown through a JSON viewer built from text nodes.
- CLI: untrusted strings are printed as Rich `Text` (markup is never interpreted) after
  removing C0 and C1 control characters other than tab and newline, and the bidi override and
  isolate characters U+202A to U+202E and U+2066 to U+2069. `--json` output escapes non-ASCII.

### Strict import validation

The dataset loader reads at most 5 MiB per file (200 JSON files per directory, symlinks
escaping the directory skipped), accepts UTF-8 only, and rejects duplicate keys, `NaN` and
`Infinity`, integers beyond ±(2^53 − 1), unpaired surrogates and nesting deeper than 32,
before strict Pydantic validation with bounded strings and collections and no user-supplied
regular expressions. Lint refuses contradictory cases, and import refuses any value the
redactor would rewrite and any key it would rename, without echoing the key
([docs/EVALUATION.md](docs/EVALUATION.md#loading-and-lint)). The dashboard sends the file's
text byte for byte (picked files must be valid UTF-8), so it gets exactly the same checks as
the CLI.

Free text that is persisted (`EvalCase.task`, `EvalCase.title`, the `task` of
`POST /api/runs` and `agentscope run`) refuses NUL characters and unpaired surrogates at the
boundary: PostgreSQL rejects NUL and strict UTF-8 cannot encode a lone surrogate, so either
would otherwise fail a save later. Model output and tool text are sanitized instead (the
characters become U+FFFD), so a model cannot make a stored run unreadable.

### Bounded execution

Every run is bounded: `max_steps` ≤ 50, `tool_timeout_s` ≤ 30, `run_timeout_s` ≤ 600,
`request_timeout_s` ≤ 600, at most 3 tool retries, decision repairs and output repairs,
`max_output_tokens` ≤ 4096 (always sent to real providers), tool outputs ≤
`max_tool_output_chars`, model replies ≤ 100,000 characters with a decision object nested at
most 32 levels deep. Runtime retries apply only to `timeout`, `error` and `malformed`
outcomes; a configuration that asks to retry anything else is refused. Per-case overrides
can only tighten limits. The API runs at most 2 jobs at once with 16 queued (`429` beyond
that). Provider status probes have a 2-second request timeout and a 5-second overall
deadline. Parsing model replies, normalizing answers and redacting text all run in linear
time, so a hostile reply or dataset value cannot stall a worker.

### Container mode

The Docker image runs `agentscope serve --container` (also `AGENTSCOPE_CONTAINER=true`),
which binds `0.0.0.0` inside the container's own network namespace without an API token,
while the Host check still accepts only loopback names. Publish the port on `127.0.0.1` only
(`-p 127.0.0.1:8765:8765`), as `docker-compose.yml` does. The Host check stops browsers, not
a client that reaches the port directly and sends a loopback `Host` itself, so publishing on
`0.0.0.0` or a LAN address would expose an unauthenticated API to your network; the server
prints a warning saying so at startup. Container mode refuses `--allowed-host` without remote
mode; for network access use `--container --allow-remote --allowed-host <name>` with
`AGENTSCOPE_API_TOKEN`.

The image runs as the unprivileged user `agentscope` (uid 10001) with its data in `/data`
(mode `0700`). The Compose file also runs the app with a read-only root filesystem, all
capabilities dropped and `no-new-privileges`, keeps PostgreSQL on an internal network with no
published port and no route out, and never publishes the optional Ollama service.

### Data at rest and data integrity

- A SQLite database directory that AgentScope creates itself gets mode `0700` and a
  `.gitignore` containing `*`; an existing directory (a project root, `/tmp`) is left as it
  is. The database file is created `0600`. See [PRIVACY.md](PRIVACY.md).
- Work in flight records its owner and a heartbeat, and recovery marks it interrupted only
  when its process is gone, so one process can never turn another's live suite into an error
  ([ARCHITECTURE.md](ARCHITECTURE.md#liveness-and-recovery)).
- Purge never deletes the built-in datasets, which every evaluation of a built-in agent needs.

## Remote mode

`agentscope serve --allow-remote` (or `AGENTSCOPE_ALLOW_REMOTE=true`) permits a non-loopback
bind. It refuses to start without `AGENTSCOPE_API_TOKEN`, and refuses tokens shorter than 16
characters. Every request, `GET`s and static files included, must then carry
`Authorization: Bearer <token>`, compared in constant time; failures answer `401`. The Host
check still applies, so remote clients' hostnames must be passed with `--allowed-host`.

Limitations:

- **The dashboard does not work in remote mode.** The single-page app never sends the token,
  and a browser does not add a bearer header on its own, so even `index.html` answers 401.
  Remote mode is for API clients.
- AgentScope serves plain HTTP. In remote mode the token and all data cross the network
  unencrypted unless TLS is terminated in front of it.
- There is one shared token, no users and no roles.

## Known limitations

- **npm advisory GHSA-vfj7-8cjw-p6xm (`braces`)**: `npm audit` reports a high-severity
  stack-exhaustion issue in `braces`, reached through `@vue/eslint-config-typescript` →
  `fast-glob` → `micromatch`. Every `braces` version is affected, so there is no patched
  release; npm's only suggestion, `npm audit fix --force`, would downgrade
  `@vue/eslint-config-typescript` to 14.0.1, a breaking change. The package runs only at lint
  time on patterns in the repository's own ESLint configuration and is not part of the built
  dashboard; `npm audit --omit=dev --audit-level=high`, which CI runs, reports 0
  vulnerabilities.
- **Purge on PostgreSQL**: purge deletes rows and runs `VACUUM FULL` on the affected tables,
  but AgentScope cannot erase copies PostgreSQL keeps elsewhere (write-ahead log segments and
  archives, base backups, replicas) or blocks the filesystem has freed. On SQLite, purge uses
  `secure_delete`, `VACUUM` and a WAL truncate, which still cannot reach backups or snapshots
  of the file. See [PRIVACY.md](PRIVACY.md#retention-and-purge).
- **The injection scanner is advisory only.** Its six heuristic signatures record
  `suspicious_content` events for analysis; they block nothing, have no measured precision or
  recall, and are not used by the evaluator.
- **Runtime containment stops execution, not text.** The permission guard prevents
  unauthorized tool execution, but a compromised model can still put forbidden content in its
  answer or in the arguments of a call that is then blocked. The evaluator detects that
  (`policy_compliance`); it does not prevent it. The demo run `demo-injection-compliant-model`
  shows both.
- **The local API has no authentication.** Loopback is not a user boundary: any process or
  user on the machine that can connect to the port can read and change AgentScope's data.
- **Redaction is pattern-based.** A secret that matches no configured value, key name or
  pattern is stored as written, and a configured secret too short or too plain to match by
  value (for example a database password `agentscope`) is redacted only inside URLs and
  `name=value` text. Do not put real secrets in tasks or datasets, and use long random values
  for configured secrets. The assignment rule also redacts harmless prose such as
  `token: field` in stored copies (checks are unaffected), and a dataset containing such text
  is refused on import.
- **An undelivered injection is not flagged.** When an injection fault never finds output to
  ride on (the faulted tool keeps failing, or returns nothing to inject into), the run records
  it only as `agentscope.fault.undelivered` on the tool spans, and the injection checks pass
  without having been exercised.
- **No encryption at rest.** Stored data is protected by file permissions (SQLite) or by
  PostgreSQL's own access control.
- **No interactive API docs page.** FastAPI's Swagger UI loads scripts and styles from a CDN,
  which the CSP blocks, so it is disabled. The OpenAPI document is served locally at
  `/api/openapi.json`.
- **Evaluating a dataset file does not refuse secret-shaped values.** Only import refuses
  them; `agentscope eval --dataset FILE` runs the file as given (the model still receives each
  task redacted) and stores redacted copies.

## Supply chain

- `uv.lock` is committed and installs use `--frozen`; uv resolves nothing published after
  `exclude-newer` (2026-10-09) in `pyproject.toml`. By convention (no tooling enforces it),
  new npm packages are added with `npm install --before=2026-10-09`; `npm ci` then installs
  exactly what `package-lock.json` pins.
- GitHub Actions in `.github/workflows/ci.yml` are pinned to full commit SHAs, and the
  workflow token is read-only (`permissions: contents: read`).
- CI runs `npm audit --omit=dev --audit-level=high` on every push and pull request.
