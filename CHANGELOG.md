# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this
project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

No changes yet.

## [1.0.0] - 2026-10-16

First release (V1): a local-first platform to debug, evaluate and compare AI agents.

### Added

**Agent runtime**
- LangGraph state machine with a fixed edge table (`plan`, `guard`, `act`, `observe`,
  `finish`, and `validate` in the validated variant); every edge recorded as a transition with
  a reason.
- Portable decision protocol (a flat JSON object parsed identically for every provider),
  bounded decision and output repairs, runtime tool retries, a step budget checked before every
  model call, per-tool and per-run timeouts, and the run statuses `completed`, `failed`,
  `max_steps`, `timeout` and `error` with typed error codes.
- Untrusted tool output delivered HTML-escaped inside one `<tool_output>` block, and an
  instruction-hierarchy clause for hardened prompts.
- Built-in agents `research_assistant` and `task_planner`, three versions each (baseline,
  hardened, hardened with the careful model profile).

**Tools and fault injection**
- Ten simulated tools over packaged synthetic data (documents, staff and project records,
  calendars, a safe calculator, a fixture-backed mock API, and three sandbox-only write tools),
  with arguments validated by Pydantic in strict mode and outputs checked against each tool's
  output model.
- Tool faults `timeout`, `error`, `malformed`, `incomplete`, `misleading`, `injection`,
  `permission_denied`, and model faults `invalid_json`, `empty`, `schema_violation`,
  `unknown_tool`, targeted by call number.

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
- One OpenTelemetry span per run, node, model call, tool attempt and guardrail decision, with
  GenAI semantic-convention attributes, a single non-global tracer provider and per-run span
  collection.
- Redaction at write time (configured secrets, sensitive key names, secret-shaped values) and
  bounded payloads.
- Provider-reported token usage only, and cost only from configured per-model rates.
- Opt-in OTLP/HTTP export behind the egress policy, without payloads unless enabled.

**Datasets**
- Hardened loader (size, depth, duplicate keys, non-finite numbers, integer range, strict
  validation, JSON-pointer issues), directory convention for gate inputs, linter for
  contradictory or unsatisfiable cases, canonical export, and two built-in datasets (29 and 27
  cases).

**Evaluation**
- Ten deterministic checks with three states, reason codes and typed evidence linked to spans;
  an evaluator that imports nothing from the runtime.
- Suite metrics as counts and rates, with informational timings excluded from baselines.
- Regression gates with exact rational arithmetic, canonical baselines written only by
  `--write-baseline`, and an environment fingerprint and effective configuration hash.
- Case-by-case comparison of two suites with per-check flips, configuration diffs by
  dimension, a confounded label, determinism and dataset-mismatch flags.

**Storage**
- SQLAlchemy models portable between SQLite (default) and PostgreSQL, Alembic migrations
  `0001` and `0002`, a repository that redacts every write, per-check result rows, and purge
  with compaction (`secure_delete`, `VACUUM`, WAL truncation; `VACUUM FULL` on PostgreSQL).

**API and CLI**
- FastAPI REST API with in-memory background jobs (202 + polling), exports as attachments, and
  a read-only settings view with storage inventory, egress list and privacy statements.
- CLI `agentscope run | eval | compare | traces | serve | datasets | agents | db | demo |
  version` on the same services layer, with exit codes 0, 1, 2 and 3.
- Idempotent demo seed (`agentscope demo seed`, `POST /api/demo/seed`).

**Dashboard**
- Vue 3 + TypeScript dashboard: overview with a "Start here" walkthrough, runs, a step-first
  trace explorer with evidence links and keyboard navigation, evaluations with a case-by-check
  matrix and gate checklist, compare with a step-aligned trace diff, failure analysis, agents,
  datasets (validate, import, export), settings with purge; light and dark themes with a
  contrast test.

**Continuous integration**
- Backend job (Ruff, mypy, pytest with PostgreSQL on Python 3.12 and 3.13), frontend job
  (production dependency audit, ESLint, vue-tsc, Vitest, build), and a quality-gate job that
  gates `research_assistant@v2` and `task_planner@v2` on the fake provider.

**Documentation**
- README, architecture, security and privacy policies, contributing guide, and guides to the
  agent runtime, tool security, evaluation, tracing and local development; issue and pull
  request templates.

### Security

- Loopback binding, Host check against DNS rebinding, mutation guard (JSON content type,
  `X-AgentScope-Client` header, same-origin and `Sec-Fetch-Site` checks), strict
  Content-Security-Policy and security headers, request body limits, and errors without
  tracebacks.
- Remote mode only with a bearer token of at least 16 characters on every request.
- No telemetry: LangSmith tracing forced off and `OTEL_*` settings ignored at startup; no
  implicit `.env` file.
- SQLite data directory `0700` and database file `0600`; datasets with secret-shaped values
  refused on import.
