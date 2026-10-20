# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this
project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Ablation attribution (#39): explains a confounded comparison by evaluating the same dataset
  version with one configuration dimension changed at a time, forward (A + d) and backward
  (B − d), and with forward pairs (A + d1 + d2) for up to 6 dimensions. It reports each
  dimension's effect in percentage points over the compared cases, the cases it gains or
  loses, each pair's interaction, the interaction left over (total minus the sum of the
  forward effects), and for every regression or fix the dimensions that are sufficient or
  necessary for it and the pairs that are sufficient together. Effects are exact fractions
  rounded once. Differences no configuration field expresses (the agent, resolved content,
  the AgentScope version, tool data) are reported as not ablated.
- `agentscope ablate (--experiment ID | --dataset ... --a A@V --b A@V) [--no-pairs]`, with
  `--json`, `--output` and `--save/--no-save`, and exit codes 0, 2 and 3.
- `POST /api/ablations` (job kind `ablation`), `GET /api/ablations` (filter `experiment_id`,
  paged with `X-Total-Count`) and `GET /api/ablations/{id}`; `GET /api/evaluations` gains
  `include_ablations`.
- Migration `0004`: the `ablations` table and `suite_runs.ablation_id`. An ablation's
  variant suites are ordinary stored suites linked to it, left out of the evaluation list,
  the overview's suite count, recent suites and pass-rate series, and the failure analysis's
  default selection (their runs are listed like any other); an ablation that does not finish
  deletes the variant suites it stored; purging experiments also purges ablations, and a
  purged ablation takes its variant suites.
- The demo seed adds an ablation of `Demo: research v1 vs v2`, reusing its suites: the
  instruction-hierarchy prompt and untrusted-output isolation add nothing alone and +41.4 pp
  together.

## [1.0.0] - 2026-10-16

First release (V1): a local-first platform to debug, evaluate and compare AI agents.

### Added

**Agent runtime**
- LangGraph state machine with a fixed edge table (`plan`, `guard`, `act`, `observe`,
  `finish`, and `validate` in the validated variant); every edge recorded as a transition with
  a reason.
- Portable decision protocol (a flat JSON object parsed identically for every provider, in
  linear time, reading only top-level objects so string contents are never rewritten),
  bounded decision and output repairs, runtime tool retries restricted to retryable outcomes
  (`timeout`, `error`, `malformed`), a step budget checked before every model call, per-tool
  and per-run timeouts, and the run statuses `completed`, `failed`, `max_steps`, `timeout`
  and `error` with typed error codes.
- A guard that checks tool existence, then permission, then strict arguments; unknown tools
  get their own `unknown_tool` outcome.
- The task is redacted before the model sees it. Untrusted tool output is delivered
  HTML-escaped inside one `<tool_output>` block, and hardened prompts carry an
  instruction-hierarchy clause.
- Built-in agents `research_assistant` and `task_planner`, three versions each (baseline,
  hardened, hardened with the careful model profile).

**Tools and fault injection**
- Ten simulated tools over packaged synthetic data (documents, staff and project records,
  calendars, a safe calculator, a fixture-backed mock API, and three sandbox-only write tools),
  with arguments validated by Pydantic in strict mode and outputs checked against each tool's
  output model.
- Tool faults `timeout`, `error`, `malformed`, `incomplete`, `misleading`, `injection`,
  `permission_denied`, and model faults `invalid_json`, `empty`, `schema_violation`,
  `unknown_tool`, targeted by call number. Failure faults apply by call position; output
  faults stay owed until they are actually delivered, and an attempt whose tool failed or left
  nothing to rewrite is marked `agentscope.fault.undelivered` on its span.

**Model providers**
- Deterministic fake provider with the `scripted-careful`, `scripted-naive` and
  `scripted-compliant` profiles (brain version 2), going through the real parse path and
  reporting no token usage.
- Ollama provider (local; enabled by default, contacted only when selected or probed; never
  downloads models) and an opt-in OpenAI-compatible provider.
- Egress policy built from settings alone, with fixed routes per target, no redirects and no
  proxy settings from the environment for provider requests and OTLP export; provider status
  probes.

**Tracing**
- One OpenTelemetry span per run, node, model call and tool attempt, plus a guardrail span for
  each blocked tool call and each tool output the scanner flags, with GenAI
  semantic-convention attributes (`gen_ai.response.finish_reasons` as a string array)
  and `error.type` on failed spans; a single non-global tracer provider and per-run span
  collection.
- Redaction at write time: configured secrets that are distinctive enough to match by value,
  secret-like key names matched by word, secret-shaped values including URL passwords,
  `name=value` assignments (keeping the name), secrets in object keys, and NUL or unpaired
  surrogate characters; bounded payloads.
- Provider-reported token usage only, and cost only from configured per-model rates.
- Opt-in OTLP/HTTP export behind the egress policy, without payloads or error messages
  unless enabled.

**Datasets**
- Hardened loader (size, depth, duplicate keys, non-finite numbers, integer range, strict
  validation, NUL-free tasks and titles, JSON-pointer issues), directory convention for gate
  inputs, linter for contradictory or unsatisfiable cases, canonical export, and two built-in
  datasets (29 and 27 cases).

**Evaluation**
- Ten deterministic checks with three states, reason codes and typed evidence linked to spans;
  an evaluator that imports nothing from the runtime.
- Normalized answer matching (`NORMALIZER_VERSION` 3) that trims sentence punctuation, quotes
  and brackets but keeps signs and symbols.
- Error recovery judged per failed attempt (recovered by the same tool or a declared
  `success_criteria.recovery_tools` entry), graceful degradation judged on every answer field
  of the agent's output schema.
- Suite metrics as counts and rates, with informational timings excluded from baselines.
- Regression gates with exact rational arithmetic, canonical baselines written only by
  `--write-baseline`, an environment fingerprint with per-component resolved content, and an
  effective configuration hash. Provider failures are harness errors, never agent failures.
- Case-by-case comparison of two suites with per-check flips, configuration diffs by
  dimension (configuration fields, resolved prompt and tool-schema content, AgentScope
  version and tool data), a confounded label, warnings, separate harness-error cases, and
  determinism and dataset-mismatch flags.

**Storage**
- SQLAlchemy models portable between SQLite (default) and PostgreSQL, Alembic migrations
  `0001` to `0003`, a repository that redacts every write, per-check result rows, list pages
  and totals from one statement, and purge with compaction (`secure_delete`, `VACUUM`, WAL
  truncation; `VACUUM FULL` on PostgreSQL) that keeps the built-in datasets.
- Liveness of in-flight work: running suites record their owner (`host:pid:incarnation`) and a
  heartbeat, and recovery marks only work whose process is gone or silent.

**API and CLI**
- FastAPI REST API with in-memory background jobs (202 + polling), paged lists with
  `limit`/`offset` and totals (`X-Total-Count` on evaluations and experiments), idempotent
  comparison of two stored suites, exports as attachments, purge conflicts with a `code`, and
  a read-only settings view with storage inventory, egress list and privacy statements. The
  OpenAPI document is served at `/api/openapi.json`; there is no Swagger UI page.
- CLI `agentscope run | eval | compare | traces | serve | datasets | agents | db | demo |
  version` on the same services layer, with exit codes 0, 1, 2 and 3; `run --expect-field`
  and `--match`; `eval --baseline` alone gates on new regressions; output destinations
  checked before a suite runs; simulated-model labels on runs, evaluations and comparisons.
- Idempotent demo seed (`agentscope demo seed`, `POST /api/demo/seed`).

**Dashboard**
- Vue 3 + TypeScript dashboard: overview with a "Start here" walkthrough, runs, a step-first
  trace explorer with evidence links and keyboard navigation, evaluations with a case-by-check
  matrix and gate checklist, compare with a step-aligned trace diff, failure analysis, agents,
  datasets (validate, byte-exact import, export), settings with purge; light and dark themes
  with a contrast test.

**Container**
- Docker image with the API and the dashboard, running as an unprivileged user with data in
  a `/data` volume and a health check.
- Container mode (`agentscope serve --container`, `AGENTSCOPE_CONTAINER`): binds `0.0.0.0`
  inside the container without an API token while accepting only loopback `Host` headers,
  for a port published on `127.0.0.1` only.
- Compose stack with PostgreSQL on an internal network and the app published on
  `127.0.0.1:8765` with a read-only filesystem and no capabilities, plus an override that adds
  a local Ollama server.

**Continuous integration**
- Backend job (Ruff, mypy, pytest with PostgreSQL on Python 3.12 and 3.13), frontend job
  (production dependency audit, ESLint, vue-tsc, Vitest, build), a quality-gate job that
  gates `research_assistant@v2` and `task_planner@v2` on the fake provider, a Playwright
  end-to-end job against the real stack over a seeded database (including accessibility
  scans and hostile-content rendering), and a Docker job that builds the image, validates the
  Compose files, checks its exposure and runs the gate inside the container.

**Documentation**
- README, architecture, security and privacy policies, contributing guide, and guides to the
  agent runtime, tool security, evaluation, tracing and local development; screenshots
  generated by Playwright; issue and pull request templates.

### Security

- Loopback binding, Host check against DNS rebinding, mutation guard (JSON content type,
  `X-AgentScope-Client` header, same-origin and `Sec-Fetch-Site` checks), strict
  Content-Security-Policy and security headers, request body limits, and errors without
  tracebacks.
- Remote mode only with a bearer token of at least 16 characters on every request.
- No telemetry: LangSmith tracing forced off and `OTEL_*` settings ignored at startup; no
  implicit `.env` file.
- SQLite database file `0600`, and data directories that AgentScope creates `0700`; datasets
  with secret-shaped values or keys refused on import.

### Fixed before release

A pre-release review confirmed 52 findings, tracked in
[#30](https://github.com/mateoosoriodelhonte/agentscope/issues/30) and fixed in pull requests
#33 to #36 and the release preparation commits. Grouped:

- **Evaluation semantics**: `error_recovery` accepted a later success of any tool as
  recovery, judged only the first failure, and read only an `answer` field for graceful
  degradation; it now judges every failure, accepts only the same tool or a declared
  `recovery_tools` entry, and requires every answer field of a declining output to be empty.
  `answer_correctness` fails a declining status unless the case expects it; `one_of` passes
  on any hit; runs without output are no longer counted as invalid outputs;
  `must_complete: false` can now pass; `timeout_behavior` bounds consecutive timed-out
  attempts across calls and fails `max_steps` runs; a long model-supplied status no longer
  crashes evaluation.
- **Matching and leak detection**: normalized matching kept treating `-3` as `3` and `C++` as
  `C` (normalizer version 3 keeps signs and symbols); canaries split by whitespace escaped
  `policy_compliance`; spelling variants of a forbidden tool name escaped
  `forbidden_tool_calls`; edge trimming was quadratic.
- **Gates and comparison**: provider errors and timeouts are harness errors (exit 3, failed
  jobs, no baseline written) instead of agent failures; harness errors are neither
  regressions nor fixes; `eval --baseline` without thresholds is no longer ignored;
  comparisons record and diff resolved content, the AgentScope version and tool data, and
  carry warnings.
- **Runtime and tools**: output faults were consumed even when nothing was delivered; the
  decision parser rewrote fences and `<think>` tags inside JSON strings, was quadratic on
  some replies, and turned deeply nested replies into harness errors; the unknown-tool model
  fault counted as an unauthorized attempt; a tool call interrupted by the run deadline was
  dropped from the trace.
- **Redaction and tracing**: quadratic JWT and private-key patterns; weak configured secrets
  (such as a database password `agentscope`) rewrote ordinary words; secrets in object keys
  and under names such as `SECRET_KEY` or `githubToken` survived; NUL and lone surrogates
  could make stored runs unreadable; redaction markers could push a field past its length
  limit; with content export off, tool arguments still left the process in span status
  descriptions; the model received unredacted tasks.
- **Storage, API and CLI**: any command marked another process's live work as interrupted
  (now owner and heartbeat, migration `0003`); opening a SQLite database changed the
  permissions of an existing directory and wrote a `.gitignore` there; purge deleted the
  built-in datasets; evaluation and experiment lists were silently capped at 50; a refused
  purge looked like a partial one; `/api/settings` could show URL passwords; a database from
  a newer release produced a generic error; `run` accepted tasks the API rejects and
  mislabelled invalid input as an internal error; unwritable output paths failed after the
  whole suite had run; the dataset detail could not be posted back; page totals could
  disagree with their pages; comparing the same suites stored duplicates; help text hid the
  `NAME@VERSION` syntax; NUL characters in tasks are now refused at the boundary.
- **Dashboard**: dataset import re-serialized the file and so bypassed the loader's strict
  checks; lists never paged; the evaluation view did not reload while following a job;
  validation issues from the API were not shown; the New run drawer could not be closed
  while a run executed; simulated-model labels were missing on the Failures page; the
  pass-rate chart mixed datasets and drew one gate threshold across every agent; cost said
  "not configured" when tokens were not reported; collapsing the span tree lost keyboard
  focus; the first-run empty state was unreachable; run ids could not be copied for Compare
  runs.
