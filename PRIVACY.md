# Privacy

AgentScope keeps what it records in one database on your machine (or in a PostgreSQL
database you configure), sends no telemetry, and contacts no network destination that your
`AGENTSCOPE_*` settings do not declare. This document lists exactly what is stored, where,
what never leaves the process, how secrets are redacted, and how to delete data.
`GET /api/settings` and the dashboard's Settings page show the same information for your
running instance: the storage location and row counts, every allowed egress destination, and
plain-language privacy statements.

## Where data is stored

- **SQLite (default)**: `<AGENTSCOPE_DATA_DIR>/agentscope.db`, with `AGENTSCOPE_DATA_DIR`
  defaulting to `.agentscope` relative to the directory you run AgentScope from. When
  AgentScope creates the directory, it sets mode `0700` and writes a `.gitignore` containing
  `*`; a directory that already exists is left as it is. The database file is created with
  mode `0600`. The database runs in WAL mode, so `agentscope.db-wal` and `agentscope.db-shm`
  exist next to it while it is open, with the same mode. In the Docker image the directory is
  the `/data` volume.
- **PostgreSQL**: the database named by `AGENTSCOPE_DATABASE_URL`. Access control is
  PostgreSQL's. The URL, including its password, is treated as a secret: it is masked in
  `/api/settings` and `agentscope db info` and redacted from traces. The password is also
  redacted on its own, by value when it is distinctive enough (see [Redaction](#redaction))
  and always inside URLs and `password=...` text.

`agentscope db info` prints the location (password masked), schema revision, size and row
count per table.

`--no-save` (on `run`, `eval` and `ablate`) keeps that run, suite or ablation (with every
suite it evaluates) out of the database, but the command still opens the database, creating and
migrating it when needed, and registers the built-in datasets in it. `agentscope compare`
always stores its two suites and the comparison.

## What is stored, table by table

Every value below is redacted before it is written (see [Redaction](#redaction)).

| Table | Field groups |
|---|---|
| `runs` | Identity and configuration: run and trace ids, agent and version, configuration hashes, provider and model, the full agent configuration, effective limits and permitted tools. Content: the task text, the final output, every tool call with its arguments, outputs, attempts and denial reasons, state transitions with their reasons, guardrail events, injected tool faults (with payloads) and model faults. Outcome: status, error code and error text, step and call counts, provider-reported token usage and cost estimate when available. Evaluation: the test case the run was judged against (expected answers and forbidden substrings included) and the verdict. Timestamps and duration. `owner` and `heartbeat_at` columns exist for a run stored while in flight; runs are stored only once finished, so they stay empty. |
| `spans` | Per span: trace, span and parent ids, name, kind, status and status message, start and end time, attributes (model parameters, tool names, outcomes, hashes), events, and input and output payloads. Payloads include the full messages sent to the model (system prompt included), the model's replies, tool arguments and results, guard decisions and scanner excerpts. |
| `datasets` | Name, version, content hash, description, agent, case count, tags, and the complete dataset document. |
| `suite_runs` | Label, status, dataset name, version and hash, agent reference, configuration hashes, provider and model, whether it was deterministic, the agent configuration, enabled checks, environment fingerprint (AgentScope, check engine, Python and LangGraph versions, a hash of the packaged tool data, provider, model, and hashes of the resolved prompt text, tool schemas, output schema, graph and fake brain version), metrics (including a token usage summary and timings), gate result, counts, timestamps, and an error message for suites that did not finish. Its owner, the machine's hostname, process id and a random per-process value (`host:pid:incarnation`), and the time of its last heartbeat, so recovery can tell whether its process still lives; both are written when the suite starts and kept after it finishes. For an ablation's variant suite, the id of that ablation. |
| `case_results` | Per case: id and content hash, link to its run, verdict, run status, harness error text, and the full case result (check messages, expected and actual values, evidence). |
| `check_results` | Per case and check: check name, status and reason code. |
| `experiments` | Name, dataset name, version and hash, links to the two suites, the full comparison (configuration differences, metric deltas, per-case and per-check changes), and summary counts. |
| `ablations` | Name, creation time, link to the comparison it explains (if any), dataset name, version and hash, links to the A and B suites and their agent references, the ablated dimensions, total effect and interaction, whether every suite was deterministic, and the full ablation (configuration differences not ablated, each variant's agent reference, configuration hash, suite id, counts, effects and the case ids it gained or lost, pair interactions, per-case attribution with run ids, warnings). The variant suites themselves are rows of `suite_runs`, with their runs and spans. |
| `alembic_version` | The schema revision. |

What is **not** stored: provider API keys, the API token, OTLP header values and the database
password (they are read from the environment, masked in `/api/settings` and redacted wherever
they would appear); the per-run tool sandbox (only the tool calls and their outputs, like any
other call); background job state, which lives in server memory only.

Other places data can end up, all under your control:

- what the CLI prints, and files you ask it to write (`eval --output`, `compare --output`,
  `eval --write-baseline`, `datasets export`); baselines contain case ids, hashes, verdicts
  and metrics, but no task text, outputs or payloads;
- JSON downloads from the dashboard or the export endpoints;
- server logs on standard error: warnings and, for unhandled errors, only the exception type,
  method and path;
- the browser's local storage, which holds only dashboard preferences: the theme, whether the
  Overview's "Start here" card was dismissed, and split-pane sizes.

## What is never stored or sent

- **No telemetry.** AgentScope sends no analytics, usage statistics or crash reports.
- **LangSmith is forced off.** LangGraph depends on langchain-core, which can upload run trees
  to LangSmith when certain environment variables are set. At import time AgentScope sets
  `LANGSMITH_TRACING`, `LANGCHAIN_TRACING_V2` and `LANGCHAIN_TRACING` to `false` and removes
  `LANGSMITH_API_KEY`, `LANGCHAIN_API_KEY`, `LANGSMITH_ENDPOINT` and `LANGCHAIN_ENDPOINT`;
  each run also disables LangSmith tracing and passes no callbacks to LangGraph.
- **`OTEL_*` is ignored.** `OTEL_EXPORTER_OTLP_*`, `OTEL_SPAN_*`, `OTEL_ATTRIBUTE_*`,
  `OTEL_TRACES_EXPORTER` and `OTEL_SDK_DISABLED` are removed from the process environment; the
  tracer provider is never installed globally, and export is configured only through
  `AGENTSCOPE_*` settings.
- **No implicit `.env`.** Settings come from the environment only.
- **Tools never reach the network**, the host filesystem or a shell; they answer from
  packaged synthetic data.
- **The dashboard talks only to its own origin** (`connect-src 'self'` in the CSP) and loads
  fonts and scripts from the server itself.

## Optional egress

Every outbound connection goes to a destination taken from the egress policy described in
[docs/TOOL_SECURITY.md](docs/TOOL_SECURITY.md#the-provider-egress-policy). All of it is opt-in
or loopback by default:

| Destination | Default | What is sent | When |
|---|---|---|---|
| Ollama (`AGENTSCOPE_OLLAMA_URL`) | enabled, `http://127.0.0.1:11434` (loopback) | during a run: prompts (system prompt, the task with secrets redacted, tool outputs), the decision schema, sampling options; a status probe sends only `GET /api/version` and `GET /api/tags` | when a run selects provider `ollama`, and when provider status is probed (`/api/providers`, the Overview page, `/api/overview`) |
| OpenAI-compatible server (`AGENTSCOPE_OPENAI_COMPATIBLE_URL`) | disabled | the same during a run; a status probe sends only `GET /models`; every request carries the API key as a bearer token if one is configured | when enabled and a run selects it, or its status is probed |
| OTLP collector (`AGENTSCOPE_OTLP_ENDPOINT`) | unset | redacted span structure and GenAI metadata, with the status of a failed span reduced to its error type (tool error messages can echo task data); payloads and full status messages only with `AGENTSCOPE_OTLP_EXPORT_CONTENT=true` | for every run once configured |

Any non-loopback destination must also be listed in `AGENTSCOPE_ALLOWED_EGRESS_HOSTS`;
credentials and OTLP headers are sent to non-loopback hosts only over https; enabling a
non-loopback target logs a warning that data will leave the machine. `agentscope serve`
prints every allowed destination at startup. AgentScope never downloads models.

## Redaction

Redaction (`src/agentscope/tracing/redaction.py`) runs on the task before the model sees
it, when data is written to a span, again in the repository before anything is saved, and on
everything returned to the CLI or the API. It replaces:

- the exact value of every configured secret (API keys, the API token, OTLP header values, the
  database URL and its password) with `[REDACTED:configured_secret]`, when the value is
  distinctive enough not to be an ordinary word: 16 or more characters anywhere, 8 to 15
  characters mixing at least two character classes as whole tokens; weaker values are not
  matched by value, and AgentScope logs a warning with their number;
- the whole value under a secret-like key name (`api_key`, `password`, `SECRET_KEY`,
  `AWS_SECRET_ACCESS_KEY`, `githubToken`, `authorization`, `cookie`, `session_id`,
  `credentials` and similar, matched by word, plus any name in `AGENTSCOPE_REDACT_KEYS`) with
  `[REDACTED]`;
- secret-shaped substrings anywhere in text (PEM private keys, `sk-` and `sk-ant-` keys,
  GitHub, AWS and Slack tokens, JWTs, `Bearer` credentials, the password in
  `scheme://user:password@host`) with `[REDACTED:<kind>]`, and the value of `password=...`
  style assignments with `[REDACTED:assignment]`, keeping the name;
- object keys that contain a secret, which are renamed to their redacted form.

NUL characters and unpaired surrogates are replaced with U+FFFD. The full rules are in
[docs/TRACING.md](docs/TRACING.md#redaction-at-write-time). Redaction is pattern-based: a
secret that matches nothing is stored as written, so do not put real credentials in tasks or
datasets.

## Datasets must not contain secrets

Importing a dataset (`agentscope datasets import`, `POST /api/datasets`) is refused when any
value would be changed by redaction, or any object key renamed, because storing it would
silently alter the dataset and its hash. The error names each affected location (for a
renamed key, its parent object, so the key itself is not repeated). Use synthetic canaries
such as `AS-CANARY-...` to test for leaks. `agentscope datasets validate` reports the same
problems without importing. Running a dataset file directly with `agentscope eval` does not
store the dataset, and everything the suite stores is redacted.

## Retention and purge

Nothing expires automatically: data stays until you delete it.

```sh
agentscope db purge --yes                                  # everything
agentscope db purge --scope runs --yes                     # runs and their spans
agentscope db purge --scope evaluations --before 2026-10-01 --yes
```

The API equivalent is `POST /api/settings/purge` with
`{"confirm": "PURGE", "scope": "all", "before": null}` (also on the Settings page). While
background jobs are running it answers 409 with `code: "jobs_running"` and deletes nothing.

| Scope | Deletes | Cascades |
|---|---|---|
| `all` | everything below | |
| `runs` | runs (by start time) | their spans; case results keep their verdicts but lose the link to the run |
| `evaluations` | suite runs (by start time), ablation variant suites included | their case results and check results, their runs and spans, experiments and ablations built on them, and the variant suites of those ablations |
| `experiments` | experiments and ablations (by creation time) | the variant suites of the deleted ablations, with their results, runs and spans |
| `datasets` | imported datasets (by creation time) | none |

The datasets packaged with the running release are never deleted, in any scope: they are
not user data, and every evaluation of a built-in agent needs them. A stored copy from
another release whose content differs is not protected, so a purge of `datasets` or `all`
deletes it; the current built-in versions are registered again right after every purge.

`--before` takes an ISO date or date-time (UTC when no time zone is given) and limits deletion
to rows created, or for runs and suites started, before it.

After deleting, AgentScope compacts storage so the content is gone from the database files,
not just unlinked:

- **SQLite**: every connection sets `PRAGMA secure_delete=ON`, so freed pages are overwritten;
  purge then runs `VACUUM` and `PRAGMA wal_checkpoint(TRUNCATE)`. If another connection is
  reading, SQLite cannot truncate the write-ahead log: the rows are gone but old page copies
  remain in the WAL, the CLI exits 3 and the API answers 409 with `code:
  "purge_incomplete"`, both with the partial result. Close other AgentScope processes and
  purge again.
- **PostgreSQL**: purge runs `VACUUM FULL` on each table that lost rows.

The result reports rows deleted per table and the storage size before and after.

Limits: AgentScope cannot erase copies outside its database files: filesystem free blocks,
SSD wear-levelling, backups and snapshots of the SQLite file, or PostgreSQL write-ahead log
segments and archives, base backups and replicas. Exports, CLI output files and baselines you
wrote are not touched by purge.
