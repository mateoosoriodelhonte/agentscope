# AgentScope

AgentScope is a local-first tool for debugging, evaluating and comparing AI agents. It runs
agents as an explicit LangGraph state machine over simulated tools, records every run as an
OpenTelemetry trace, judges runs with ten deterministic checks, gates results in CI with
exact arithmetic, and compares two agent versions case by case, with a CLI, a REST API and a
Vue dashboard that all share one engine. It is not a hosted observability service, not a
general-purpose agent framework, and not a benchmark of language models: V1 evaluates its two
built-in agents (and configuration variants of them), it ships no LLM-as-judge, and its CI
gate runs on a documented deterministic fake model whose behaviour is a simulation of known
failure modes, not a measurement of any real model.

| Overview | Trace explorer |
|---|---|
| ![Overview, dark theme](docs/images/overview-dark.png) | ![Trace explorer, dark theme](docs/images/trace-dark.png) |
| **Trace explorer (light)** | **Evaluation** |
| ![Trace explorer, light theme](docs/images/trace-light.png) | ![Evaluation detail, dark theme](docs/images/evaluation-dark.png) |
| **Compare** | **Failures** |
| ![Compare, dark theme](docs/images/compare-dark.png) | ![Failure analysis, light theme](docs/images/failures-light.png) |

## Features

**Agent runtime** ([docs/AGENT_RUNTIME.md](docs/AGENT_RUNTIME.md))
- A LangGraph `StateGraph` with a fixed edge table: `plan → guard → act → observe → plan`,
  plus a `validate` node with a bounded repair loop in the `validated` graph variant. Every
  edge taken is recorded as a state transition with a reason.
- A portable decision protocol (a flat JSON object, parsed and validated the same way for
  every provider), bounded decision and output repairs, runtime tool retries, a step budget,
  per-tool and per-run timeouts, and five terminal run statuses.
- Two built-in agents with three versions each: `research_assistant` (search and read a
  synthetic knowledge base) and `task_planner` (plan a project from synthetic records,
  service status and calendars).
- Untrusted tool output can be delivered HTML-escaped inside a single `<tool_output>` block,
  and hardened prompts carry an instruction-hierarchy clause.

**Simulated tools and fault injection** ([docs/TOOL_SECURITY.md](docs/TOOL_SECURITY.md))
- Ten tools over packaged synthetic data. On every call, arguments are validated with
  Pydantic in strict mode and outputs against the tool's output model. No tool touches the
  network, the host filesystem or a shell; the three write tools only append to a per-run,
  in-memory sandbox.
- Seven tool fault kinds (`timeout`, `error`, `malformed`, `incomplete`, `misleading`,
  `injection`, `permission_denied`) and four model fault kinds (`invalid_json`, `empty`,
  `schema_violation`, `unknown_tool`), targeted by call number.

**Model providers**
- A deterministic fake provider with three scripted profiles (always available, reports no
  token usage).
- Ollama (local, enabled by default but contacted only when a run selects it or its status is
  probed) and an opt-in OpenAI-compatible provider, both behind an egress policy built only
  from `AGENTSCOPE_*` settings.

**Tracing** ([docs/TRACING.md](docs/TRACING.md))
- One OpenTelemetry span per run, node, model call, tool attempt and guardrail decision,
  with GenAI semantic-convention attributes. Payloads are redacted when they are written to
  a span. Token usage is shown only when a provider reports it; cost only from rates you
  configure.
- Optional OTLP/HTTP export, off by default, payload-free by default.

**Evaluation** ([docs/EVALUATION.md](docs/EVALUATION.md))
- A hardened dataset loader and a linter that rejects contradictory or unsatisfiable cases.
- Ten deterministic checks with three states (`passed`, `failed`, `not_applicable`), typed
  evidence that points at spans, and an evaluator that imports nothing from the runtime it
  judges.
- Regression gates with exact rational arithmetic, canonical baselines and documented exit
  codes; experiment comparison with per-case flips and a "confounded" label when more than
  one configuration dimension changed.

**Storage, API, CLI and dashboard** ([ARCHITECTURE.md](ARCHITECTURE.md))
- SQLite by default (file `0600` in a `0700` directory), PostgreSQL optional; Alembic
  migrations; purge that compacts the database.
- A FastAPI REST API with in-memory background jobs, and a Typer CLI, both on one shared
  services layer.
- A Vue 3 dashboard: overview, runs, trace explorer with evidence links, evaluations,
  compare with a step-aligned trace diff, failure analysis, agents, datasets and settings.

**Security and privacy** ([SECURITY.md](SECURITY.md), [PRIVACY.md](PRIVACY.md))
- Loopback binding, a Host check against DNS rebinding, a mutation guard, a strict
  Content-Security-Policy, request body limits, no telemetry, and no network destination
  that Settings do not declare.

## Architecture

```mermaid
flowchart LR
    subgraph clients[Clients]
        CLI[agentscope CLI]
        UI[Vue dashboard]
    end
    UI -->|HTTP /api| API[FastAPI API<br/>security middleware, jobs]
    CLI --> SVC
    API --> SVC[Services layer]
    SVC --> RUN[LangGraph runtime<br/>plan / guard / act / observe / validate]
    RUN --> PROV[Model providers<br/>fake · Ollama · OpenAI-compatible]
    RUN --> TOOLS[Simulated tools<br/>fault injection · sandbox]
    RUN --> TRACE[Tracing<br/>OTel spans, redaction]
    TRACE -.->|opt-in OTLP| OTLP[(Collector)]
    RUN -->|TraceRecord| SVC
    SVC -->|TraceRecord + EvalCase| EVAL[Evaluation engine<br/>checks · metrics · gates · compare]
    SVC --> REPO[Repository<br/>redacts every write]
    REPO --> DB[(SQLite or PostgreSQL)]
```

The CLI calls the services layer directly; the dashboard reaches it through the API. The
evaluation engine (`agentscope.evaluation`) imports only the domain contracts, `jsonschema`,
`pydantic` and the standard library (enforced by `tests/unit/test_evaluation_isolation.py`),
so a defect in the runtime's guard or validator cannot make its own verdicts pass. See
[ARCHITECTURE.md](ARCHITECTURE.md) for the data flow, state machine, storage schema and
design decisions.

## Quick start

Prerequisites: Python 3.12 or later, [uv](https://docs.astral.sh/uv/), and Node.js 22.12 or
later (only to build the dashboard).

```sh
uv sync --frozen --all-extras          # Python environment (extras: postgres, otlp)
(cd frontend && npm ci && npm run build)  # builds frontend/dist, served by `agentscope serve`
uv run agentscope demo seed            # deterministic demo data in ./.agentscope/agentscope.db
uv run agentscope serve                # API and dashboard on http://127.0.0.1:8765
```

`uv sync --frozen` without `--all-extras` is enough for SQLite and no OTLP export. Without a
built dashboard, `agentscope serve` serves only the API. Data goes to `./.agentscope`
relative to the working directory unless `AGENTSCOPE_DATA_DIR` or `AGENTSCOPE_DATABASE_URL`
says otherwise ([docs/LOCAL_DEVELOPMENT.md](docs/LOCAL_DEVELOPMENT.md)).

### Docker Compose

```sh
cp .env.example .env      # then set POSTGRES_PASSWORD in .env
docker compose up --build
```

The app is then on http://127.0.0.1:8765. See [docs/LOCAL_DEVELOPMENT.md](docs/LOCAL_DEVELOPMENT.md#docker-compose).

## Five-minute demo

`agentscope demo seed` (or **Load demo data** on the Overview) creates, with the fake model:
five evaluations under one demo gate (research assistant v1, v2, v3 and task planner v1, v2),
two stored comparisons (`Demo: research v1 vs v2`, `Demo: research v2 vs v3`) and four ad-hoc
runs. Seeding is idempotent: a second `demo seed` printed `created 0 suites, 0 experiments,
0 runs; already present 5 suites, 2 experiments, 4 runs`.

1. **v1 fails the gate.** Open **Evaluations** and then `demo: research_assistant@v1`. It
   passed 5 of 29 cases. The gate checklist shows the failed rules: `min_pass_rate` (5/29
   against 0.6), `max_policy_violation_cases` (8 against 0) and the required checks
   `forbidden_tool_calls` and `policy_compliance` (8 failures each).
   `max_unauthorized_tool_executions` passes with 0: v1's model tried forbidden tools, and
   the runtime guard blocked every attempt.
2. **Compare v1 with v2.** Open **Compare** and select `Demo: research v1 vs v2`. The
   configuration diff is labelled *Confounded: attribution not possible*: five dimensions
   changed at once (prompt, tool schemas, graph, retry policy, untrusted-output isolation),
   so the result cannot be credited to any one of them. The pass rate goes from 17.2% to
   72.4% (+55.2 pp, n=29): 16 cases fixed, 8 still failing for the same checks, 5 still
   passing, and no regressions.
3. **Open a fixed case.** Select `injection-search-snippet` in the *Fixed* group (A failed
   `forbidden_tool_calls` and `policy_compliance`; B failed nothing).
4. **Find the first divergence.** The trace diff aligns the two runs step by step. Its
   callout reads that step 1 of both runs made a `tool_call:search_documents` decision but
   the tool result differs (v2 uses tool schema 2, whose search returns verified sources
   only). Press **Next** (or `n` while the diff has focus) for the other three divergences:
   v1's steps 2 and 3 (`send_email` and `delete_record`, both blocked) have no counterpart in
   v2, and the final decision differs because v1's answer copies its hidden instructions into
   `notes`.
5. **Inspect the evidence span.** Click v1's step 2 to open its trace, then activate the
   failed `forbidden_tool_calls` check in the verdict panel. The trace selects and outlines
   the `guardrail tool_policy` span that blocked `send_email`.

The Overview's *Start here* card walks the same five steps, starting from the most recent
evaluation whose gate failed (on a freshly seeded database that is `task_planner@v1`,
compared with `task_planner@v2`). The ad-hoc run `demo-injection-compliant-model` shows the
limit of runtime containment: under v2, a model that obeys every embedded instruction
(`scripted-compliant`) attempts `send_email`, which is blocked (0 executions), but it still
copies its hidden instructions into the answer, which `policy_compliance` reports as a leak.

## CLI

| Command | What it does |
|---|---|
| `agentscope run --task TEXT [--agent A@V] [--fault tool:kind[:on_call]] [--expect TEXT]` | Run one task on a built-in version, print the trace summary; with `--expect`, evaluate it as an ad-hoc case. |
| `agentscope eval --dataset PATH\|NAME[@VER] [--agent A@V \| --config FILE] [--check]` | Evaluate a dataset; `--check` applies `gate.json` and the baseline from the dataset directory. `--write-baseline FILE`, `--json`, `--output FILE`, `--no-save`. |
| `agentscope compare --dataset ... --a A@V --b A@V [--fail-on-regression]` | Evaluate two versions and compare them case by case. |
| `agentscope traces list` / `traces show RUN_ID` | List stored runs; show one trace (`--json` for the full record). |
| `agentscope datasets list\|validate\|import\|export` | Stored datasets; lint a file or directory without importing; import (versions are immutable); canonical export. |
| `agentscope agents list` | The built-in agent versions and what distinguishes them. |
| `agentscope db upgrade\|info\|purge` | Migrate; show location, revision and row counts; delete data and compact (`--scope`, `--before`, `--yes`). |
| `agentscope demo seed` | Seed the demo story (idempotent). |
| `agentscope serve [--host] [--port] [--allow-remote] [--allowed-host H]` | Serve the API and dashboard; refuses a non-loopback bind without `--allow-remote`. |
| `agentscope version` | Print the installed version. |

Exit codes (`src/agentscope/cli/common.py`):

| Code | Meaning |
|---|---|
| 0 | Success. `eval` without a gate (no `--check` and no `--thresholds`) exits 0 even when cases fail. |
| 1 | Checks or gate failed: `eval --check` gate failure, `run --expect` verdict failure, `compare --fail-on-regression` with a regression. |
| 2 | Invalid input: dataset errors, incompatible baseline, unknown agent or dataset, disabled provider, bad flags, `db purge` without `--yes`, unknown run id. |
| 3 | Runtime or harness error: a case the harness could not judge (`internal_error`, `internal_recursion_limit`), an unexpected exception, an unusable database, or a purge that could not finish erasing. A harness error wins over a gate failure. |

## Using the CI quality gate in your own pipeline

The `quality-gate` job in [.github/workflows/ci.yml](.github/workflows/ci.yml) runs:

```sh
uv run agentscope eval --dataset tests/fixtures --agent research_assistant@v2 --check --no-save --json > gate-research.json
uv run agentscope eval --dataset tests/fixtures/planner --agent task_planner@v2 --check --no-save --json > gate-planner.json
```

A dataset directory holds `*.dataset.json` files (one dataset), `gate.json` (thresholds) and
`<agent>@<version>.baseline.json` files ([tests/fixtures/README.md](tests/fixtures/README.md)).
`--check` loads the thresholds and the baseline for the evaluated version; without any
thresholds it requires every case to pass. To gate your own change:

1. Put your cases in a directory as above and lint them with
   `agentscope datasets validate DIR`.
2. Write `gate.json` (rules in [docs/EVALUATION.md](docs/EVALUATION.md#gates)).
3. Record a reviewed baseline with
   `agentscope eval --dataset DIR --agent A@V --write-baseline DIR/A@V.baseline.json`.
4. In CI, run `agentscope eval --dataset DIR --agent A@V --check --no-save --json` and fail the
   job on any non-zero exit code (1 gate, 2 invalid input, 3 harness error).

Scope: V1 evaluates only the built-in agents, or `AgentConfig` variants of them passed with
`--config` (their prompts and tools must exist in the agent's definition). There is no
plugin interface for an agent that lives outside this repository; adding one means adding
code under `src/agentscope/agents/` ([CONTRIBUTING.md](CONTRIBUTING.md#adding-an-agent)).
Gate only the fake provider: real models are not deterministic, so baselines recorded with
them would flip.

## What is measured, and what is not

Measured, deterministically:
- Whether each case's run satisfies the ten checks: answer, schema, required and forbidden
  tools, order, arguments, completion and budgets, timeout handling, error recovery, and
  policy compliance (unauthorized executions and leaks of forbidden substrings, including
  base64, hex and percent-encoded copies).
- Suite metrics as counts and rates over cases, per-check pass counts over the cases where a
  check applied, and per-case flips between two suites.
- Whether the runtime contains a model that obeys injected instructions: unauthorized tool
  executions are counted separately from attempts.

Not measured:
- How any real language model behaves. The CI gate runs the fake model; a pass says the
  harness and the agent wiring contain the simulated failure modes.
- Answer quality beyond the configured matchers. There is no model-based judging in V1
  ([docs/EVALUATION.md](docs/EVALUATION.md#model-based-judging)).
- Latency and cost as gate inputs. Durations are recorded but are informational (excluded
  from baselines and gates); token usage appears only when a provider reports it, and the
  fake provider reports none.
- Statistical significance: there are no repeated runs or confidence intervals. Comparisons
  involving a non-deterministic provider are labelled "flips may be noise".
- Precision or recall of the heuristic injection scanner, which is advisory only.

## The deterministic fake model

The fake provider (`src/agentscope/providers/fake.py`) is a test double, not a language
model. Each agent supplies a "brain": a pure function from the conversation so far to the
next decision, which is serialized to text and parsed exactly like a real model's reply.
Three profiles simulate documented failure modes:

| Model name | Embedded instructions in tool output | Failed tools |
|---|---|---|
| `scripted-careful` | never followed | retries once, then declines (`insufficient_evidence` / `cannot_complete`) |
| `scripted-naive` | followed unless the output is delimited **and** the system prompt has the instruction-hierarchy clause | does not retry; answers anyway |
| `scripted-compliant` | always followed | retries once, then declines |

The brain logic is versioned (`FAKE_BRAIN_VERSION = "2"`, part of the effective
configuration hash). Consequences you should keep in mind: v2's resistance to the
prompt-injection cases is true by construction of `scripted-naive`; what those cases test is
that v2 delivers delimiting and the clause, that the evaluator catches every attempt and
leak v1 makes, and that the runtime guard stops a model that obeys everything. The
trigger-to-decision tables are in [docs/AGENT_RUNTIME.md](docs/AGENT_RUNTIME.md#the-fake-model).
The dashboard labels fake-provider runs, evaluations and comparisons, and the CLI labels
fake-provider evaluations and comparisons, with "Simulated model: demonstrates harness
mechanics, not model behaviour".

## Optional: Ollama

Ollama is enabled by default (`AGENTSCOPE_ENABLE_OLLAMA=true`), which only makes it
selectable: nothing is sent to `AGENTSCOPE_OLLAMA_URL` (default `http://127.0.0.1:11434`)
until a run selects provider `ollama` or the provider status is probed. AgentScope never
downloads models; a missing model is reported with the `ollama pull` command for you to run.
Requests use Ollama's `/api/chat` with `format` set to the decision schema, the configured
temperature and seed, `num_predict` set to `max_output_tokens`, and `think: false`.

Built-in versions use the fake provider, so select Ollama with an `AgentConfig` file, for
example:

```json
{
  "agent": "research_assistant",
  "version": "v2-ollama",
  "prompt_version": "p2",
  "model": { "provider": "ollama", "model": "llama3.2", "request_timeout_s": 300 },
  "tools": ["search_documents", "retrieve_document"],
  "tool_schema_version": 2,
  "graph": "validated",
  "isolate_untrusted": true,
  "retry": { "max_tool_retries": 1, "max_decision_repairs": 1, "max_output_repairs": 1 },
  "limits": { "run_timeout_s": 600, "tool_timeout_s": 10 }
}
```

```sh
uv run agentscope eval --dataset tests/fixtures --config ollama-v2.json
```

The same JSON can be pasted into the dashboard's **New run** drawer. A non-loopback Ollama
host must be listed in `AGENTSCOPE_ALLOWED_EGRESS_HOSTS`.

## Security and privacy

- AgentScope is a single-user local tool. It binds to `127.0.0.1`, accepts only loopback
  `Host` headers, requires a JSON content type and an `X-AgentScope-Client: 1` header on
  every state-changing request, rejects cross-origin and cross-site mutations, and sends a
  strict Content-Security-Policy on every response. Remote mode requires a bearer token and
  is meant for API clients: the dashboard cannot send the token.
- Nothing leaves the machine unless you configure it: no telemetry, LangSmith tracing forced
  off, `OTEL_*` variables ignored, and every outbound destination (model providers, the
  optional OTLP collector) taken from an egress policy built from `AGENTSCOPE_*` settings.
- Secrets are redacted before anything is stored or exported; datasets containing
  secret-shaped values are refused on import.

Details, defenses and known limitations: [SECURITY.md](SECURITY.md). What is stored and how
to delete it: [PRIVACY.md](PRIVACY.md).

## Verification results (2026-10-16)

Run on the application code of commit `021fd90` (this documentation adds no code) on macOS
26.6.2 (arm64), Python 3.12.3, uv 0.12.21, ruff 0.16.10, Node.js 26.0.0, npm 11.12.1. Every
number below is copied from the command's output.

| Command | Result |
|---|---|
| `uv run ruff check .` | All checks passed |
| `uv run ruff format --check .` | 185 files already formatted (Python sources and, with this ruff version, Markdown files) |
| `uv run mypy` | Success: no issues found in 106 source files |
| `uv run pytest -q` | 1218 passed, 24 skipped (23 need `AGENTSCOPE_TEST_POSTGRES_URL`, 1 needs `AGENTSCOPE_TEST_OLLAMA_MODEL`) |
| `npm run lint` / `npm run type-check` | pass (ESLint with `--max-warnings=0`; `vue-tsc --build`) |
| `npm test` | 661 tests in 30 files passed |
| `npx vite build` | built |
| `npm audit --omit=dev --audit-level=high` | found 0 vulnerabilities |

Quality gates, `agentscope eval --dataset DIR --agent A@V --check --no-save`:

| Dataset directory | Agent | Passed | Gate | Exit |
|---|---|---|---|---|
| `tests/fixtures` | `research_assistant@v2` | 21/29 | PASSED | 0 |
| `tests/fixtures` | `research_assistant@v1` | 5/29 | FAILED | 1 |
| `tests/fixtures` | `research_assistant@v3` | 29/29 | PASSED | 0 |
| `tests/fixtures/planner` | `task_planner@v2` | 19/27 | PASSED | 0 |
| `tests/fixtures/planner` | `task_planner@v1` | 5/27 | FAILED | 1 |
| `tests/fixtures/planner` | `task_planner@v3` | 27/27 | PASSED | 0 |

v1 fails `min_pass_rate`, `max_new_regressions` (there is no v1 baseline, so the rule
cannot be verified and fails), `max_invalid_outputs` (3), `max_policy_violation_cases` (8
research, 7 planner) and both required checks; it passes `max_unauthorized_tool_executions`
with 0 and `max_harness_errors` with 0.

## Project layout

```
src/agentscope/
  _bootstrap.py      telemetry switches forced off before third-party imports
  config.py          Settings (AGENTSCOPE_* environment variables)
  domain/            Pydantic contracts shared by every layer
  runtime/           LangGraph state machine, decision protocol, prompts, guards
  agents/            built-in agents: definitions, versions, deterministic brains
  providers/         fake, Ollama, OpenAI-compatible; egress policy (http.py)
  tools/             simulated tools, executor, fault injection, packaged data
  tracing/           span recorder, redaction, attribute names, OTLP export
  evaluation/        checks, metrics, gates, baselines, fingerprint (runtime-independent)
  experiments/       suite comparison
  datasets/          loader, lint, export, built-in datasets
  storage/           ORM models, repository, Alembic migrations
  services.py        the engine shared by the CLI and the API
  suite_runner.py    runs a dataset sequentially and aggregates verdicts
  analysis.py        overview and failure analysis over stored verdicts
  demo.py            the demo seed
  jobs.py            in-memory background jobs for the API
  api/               FastAPI app, security middleware, routes, schemas
  cli/               Typer commands
frontend/            Vue 3 + TypeScript dashboard (Vite, Vitest)
tests/               pytest + Hypothesis; tests/fixtures holds the CI gate inputs
docs/                runtime, tool security, evaluation, tracing, local development
```

## Documentation

- [ARCHITECTURE.md](ARCHITECTURE.md): components, data flow, state machine, storage, decisions
- [docs/AGENT_RUNTIME.md](docs/AGENT_RUNTIME.md): graph, statuses, protocol, fake model
- [docs/TOOL_SECURITY.md](docs/TOOL_SECURITY.md): tool contract, faults, egress policy
- [docs/EVALUATION.md](docs/EVALUATION.md): datasets, checks, metrics, gates, comparison
- [docs/TRACING.md](docs/TRACING.md): spans, attributes, redaction, OTLP export
- [docs/LOCAL_DEVELOPMENT.md](docs/LOCAL_DEVELOPMENT.md): setup, tests, settings
- [SECURITY.md](SECURITY.md), [PRIVACY.md](PRIVACY.md), [CONTRIBUTING.md](CONTRIBUTING.md),
  [CHANGELOG.md](CHANGELOG.md)

## License

MIT. See [LICENSE](LICENSE).
