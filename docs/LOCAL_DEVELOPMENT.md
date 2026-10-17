# Local development

## Prerequisites

| Tool | Version | Needed for |
|---|---|---|
| Python | 3.12 or later (`.python-version` pins 3.12; CI tests 3.12 and 3.13) | everything |
| [uv](https://docs.astral.sh/uv/) | any recent release (the verification in the README used 0.12.21; CI installs it with `astral-sh/setup-uv`) | the Python environment |
| Node.js | 22.12 or later (`frontend/package.json` engines; CI uses 24) | building and testing the dashboard |
| PostgreSQL | optional (CI uses `postgres:18-alpine`) | PostgreSQL storage and its tests |
| Ollama | optional | running agents on a local model |
| Docker with Compose | optional | the Compose setup |

## Python (uv)

```sh
uv sync --frozen --all-extras    # dev group + extras: postgres (psycopg), otlp (OTLP exporter)
uv run agentscope --help
uv run ruff check .
uv run ruff format --check .
uv run mypy                      # strict, over src/agentscope
uv run pytest -q
```

`pyproject.toml` sets `exclude-newer = "2026-10-09T00:00:00Z"` for uv: packages published
after that date are never resolved. Bump it deliberately when updating the lockfile.

## Dashboard (npm)

```sh
cd frontend
npm ci
npm run dev          # Vite dev server on http://127.0.0.1:5173
npm run build        # vue-tsc --build, then vite build into frontend/dist
npx vite build       # build without the type-check
npm run lint         # ESLint, --max-warnings=0
npm run type-check   # vue-tsc --build
npm test             # Vitest (jsdom), src/**/*.spec.ts
npm run test:watch
```

New npm dependencies follow the same cool-off: `npm install --before=2026-10-09 <package>`.
CI also runs `npm audit --omit=dev --audit-level=high`.

`agentscope serve` serves the built dashboard from `frontend/dist` in a checkout (or from
`agentscope/web/dist` inside an installed wheel). Without a build it serves only the API.

## Running tests

The suite runs under `pytest-socket`: `--disable-socket --allow-unix-socket
--allow-hosts=127.0.0.1,::1,localhost`. A test that tries to reach any other host fails.
An autouse fixture removes every `AGENTSCOPE_*` variable except
`AGENTSCOPE_TEST_POSTGRES_URL` and points `AGENTSCOPE_DATA_DIR` at a temporary directory, so
tests never touch your `./.agentscope` or your configuration.

Markers: `postgres`, `ollama`, `slow`. Useful invocations:

```sh
uv run pytest -q                              # everything that needs no external service
uv run pytest tests/evaluation -q             # one area
uv run pytest --cov --cov-report=term         # coverage (as in CI)
```

### PostgreSQL

Tests marked `postgres` (repository and migration tests) run only when
`AGENTSCOPE_TEST_POSTGRES_URL` points at a **disposable** database: the migration tests run
`DROP SCHEMA public CASCADE` on it. For example:

```sh
docker run --rm -d --name agentscope-pg -p 127.0.0.1:5432:5432 \
  -e POSTGRES_USER=agentscope -e POSTGRES_PASSWORD=change-me -e POSTGRES_DB=agentscope_test \
  postgres:18-alpine
AGENTSCOPE_TEST_POSTGRES_URL=postgresql+psycopg://agentscope:change-me@127.0.0.1:5432/agentscope_test \
  uv run pytest -q
```

CI's backend job runs the whole suite this way against a service container.

### Live Ollama

`tests/unit/test_providers_ollama_live.py` runs a real research-assistant run against a local
Ollama server when `AGENTSCOPE_TEST_OLLAMA_MODEL` names a model you have already pulled:

```sh
AGENTSCOPE_TEST_OLLAMA_MODEL=llama3.2 uv run pytest -m ollama
```

`AGENTSCOPE_TEST_OLLAMA_URL` overrides the server (default `http://127.0.0.1:11434`); it must
be loopback, which is all `pytest-socket` allows. The variables are read at import time. The
test asserts only that the provider path works, not how well the model answers.

## The Vite dev server and its proxy

`npm run dev` binds `127.0.0.1:5173` (`strictPort`) and proxies `/api` to
`http://127.0.0.1:8765` without rewriting the `Host` header. Start the API first:

```sh
uv run agentscope serve          # terminal 1
cd frontend && npm run dev       # terminal 2, then open http://127.0.0.1:5173
```

The API accepts the proxied requests because the `Host` header (`127.0.0.1:5173`) is a
loopback name, and state-changing requests from the dev origin pass the mutation guard
because `http://127.0.0.1:5173` and `http://localhost:5173` are the default
`AGENTSCOPE_ALLOWED_ORIGINS`. A dev server on another origin needs that setting changed.

## Docker Compose

```sh
cp .env.example .env      # set POSTGRES_PASSWORD in .env (the file is git-ignored)
docker compose up --build
```

The app is served on http://127.0.0.1:8765 with PostgreSQL storage. Keep the published port
bound to `127.0.0.1`: the API is designed for loopback access only ([SECURITY.md](../SECURITY.md)).

## The data directory

- `AGENTSCOPE_DATA_DIR` defaults to `.agentscope`, relative to the directory you run
  AgentScope from. Without `AGENTSCOPE_DATABASE_URL`, the database is
  `<data dir>/agentscope.db` (SQLite, WAL mode, so an `agentscope.db-wal` file exists while it
  is open).
- For SQLite, whenever an engine is created, the database file's directory is created if
  needed and set to mode `0700`, a `.gitignore` containing `*` is written into it if missing,
  and a new database file is created with mode `0600`. The repository's own `.gitignore` also ignores `.agentscope/`,
  `*.db`, `*.sqlite`, `*.sqlite3`, `.env` and `.env.*` (except `.env.example`).
- With `AGENTSCOPE_DATABASE_URL` set (for example
  `postgresql://user:password@127.0.0.1:5432/agentscope`), that database is used instead;
  `postgresql://` and `postgres://` are rewritten to `postgresql+psycopg://`, which needs the
  `postgres` extra.
- Commands that open the engine (`serve`, `run`, `eval`, `compare`, `datasets`, `traces`,
  `agents`, `demo`, `db purge`) upgrade the schema to the latest revision first. `agentscope db
  info` never migrates; it shows the location (password masked), the revision and row
  counts. `agentscope db upgrade` migrates explicitly.
- To start over, purge (`agentscope db purge --yes`) or point `AGENTSCOPE_DATA_DIR` at another
  directory. See [PRIVACY.md](../PRIVACY.md#retention-and-purge).

## Settings

Every setting is an environment variable read by `Settings` in `src/agentscope/config.py`
(pydantic-settings, prefix `AGENTSCOPE_`, read once per process). No `.env` file is ever
loaded implicitly. List and object values are JSON, for example
`AGENTSCOPE_ALLOWED_EGRESS_HOSTS='["collector.example.com"]'`.

| Variable | Default | Meaning |
|---|---|---|
| `AGENTSCOPE_DATA_DIR` | `.agentscope` | Directory for the SQLite database (`~` expanded). |
| `AGENTSCOPE_DATABASE_URL` | unset | SQLAlchemy URL of another database (e.g. PostgreSQL). Secret: masked in `/api/settings`, `db info` and redacted from traces. |
| `AGENTSCOPE_HOST` | `127.0.0.1` | Default bind address for `serve`. A non-loopback value requires remote mode. |
| `AGENTSCOPE_PORT` | `8765` | Default port for `serve` (1 to 65535); also used in the dashboard links the CLI prints. |
| `AGENTSCOPE_ALLOW_REMOTE` | `false` | Remote mode, as `serve --allow-remote`. |
| `AGENTSCOPE_API_TOKEN` | unset | Bearer token required on every request in remote mode (at least 16 characters). Secret. |
| `AGENTSCOPE_ALLOWED_ORIGINS` | `["http://127.0.0.1:5173", "http://localhost:5173"]` | Extra `Origin` values allowed to make state-changing requests (the server's own origin is always allowed). |
| `AGENTSCOPE_MAX_REQUEST_BYTES` | `5242880` (5 MiB) | Body limit for `/api/datasets` requests (at least 1024). Other requests are limited to 256 KiB. |
| `AGENTSCOPE_ENABLE_OLLAMA` | `true` | Makes the Ollama provider selectable. Nothing is sent until a run selects it or its status is probed. |
| `AGENTSCOPE_OLLAMA_URL` | `http://127.0.0.1:11434` | Ollama server. A non-loopback host must be allowlisted. |
| `AGENTSCOPE_ALLOWED_EGRESS_HOSTS` | `[]` | Non-loopback hostnames or IP addresses that providers and the OTLP exporter may contact (exact matches). Empty means loopback only. |
| `AGENTSCOPE_ALLOWED_MODEL_HOSTS` | `[]` | Earlier name for the above; still honoured (the two lists are merged). |
| `AGENTSCOPE_ENABLE_OPENAI_COMPATIBLE` | `false` | Enables the OpenAI-compatible provider. |
| `AGENTSCOPE_OPENAI_COMPATIBLE_URL` | unset | Its base URL, e.g. `http://127.0.0.1:8080/v1`. |
| `AGENTSCOPE_OPENAI_COMPATIBLE_API_KEY` | unset | Sent as `Authorization: Bearer`; only over https to non-loopback hosts. Secret. |
| `AGENTSCOPE_OTLP_ENDPOINT` | unset | OTLP/HTTP collector (base URL or `/v1/traces` URL). Unset means no export. |
| `AGENTSCOPE_OTLP_HEADERS` | `{}` | JSON object of headers for the collector; values are secrets. Sent to non-loopback hosts only over https. |
| `AGENTSCOPE_OTLP_EXPORT_CONTENT` | `false` | Also export span payloads (task, prompts, tool arguments and results). |
| `AGENTSCOPE_MODEL_RATES` | `{}` | Per-model prices, e.g. `{"llama3.2": {"input_per_1k_tokens": 0.1, "output_per_1k_tokens": 0.2}}`. Costs are shown only for configured models. |
| `AGENTSCOPE_REDACT_KEYS` | `[]` | Additional attribute or field names to redact (exact, case-insensitive). |

Nested values can also be set with the `__` delimiter (for example
`AGENTSCOPE_OTLP_HEADERS__X_TOKEN=...` gives a header named `x_token`), but JSON keeps names
exact.

Variables read only by the test suite: `AGENTSCOPE_TEST_POSTGRES_URL`,
`AGENTSCOPE_TEST_OLLAMA_MODEL`, `AGENTSCOPE_TEST_OLLAMA_URL`.

AgentScope also overrides some third-party variables at startup: `LANGSMITH_TRACING`,
`LANGCHAIN_TRACING_V2` and `LANGCHAIN_TRACING` are forced to `false`; `LANGSMITH_API_KEY`,
`LANGCHAIN_API_KEY`, `LANGSMITH_ENDPOINT`, `LANGCHAIN_ENDPOINT`, `OTEL_EXPORTER_OTLP_*`,
`OTEL_SPAN_*`, `OTEL_ATTRIBUTE_*`, `OTEL_TRACES_EXPORTER` and `OTEL_SDK_DISABLED` are removed
from the process environment, with one warning naming them (never their values).

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `400 {"detail": "invalid host header"}` | The `Host` header is not `127.0.0.1`, `localhost` or `::1`. Open the app through one of those, or (remote mode) pass `--allowed-host NAME`. |
| `403 missing X-AgentScope-Client header`, or `must use Content-Type: application/json` | Scripts must send both headers on POST requests: `curl -X POST -H 'Content-Type: application/json' -H 'X-AgentScope-Client: 1' ...`. |
| `403 cross-origin request rejected` / `cross-site request rejected` | The request came from another origin. For a dev server on another port, add its origin to `AGENTSCOPE_ALLOWED_ORIGINS`. |
| `413 request body exceeds ... bytes` | 256 KiB for most requests; dataset requests up to `AGENTSCOPE_MAX_REQUEST_BYTES`. |
| `429` when starting work | 2 jobs are running and 16 queued; wait for some to finish. |
| The dashboard does not load at `/` | Build it: `cd frontend && npm run build`, then restart `serve`. |
| `error: refusing to bind 0.0.0.0` (exit 2) | Non-loopback binds need `--allow-remote` and `AGENTSCOPE_API_TOKEN` of at least 16 characters. |
| `database revision ... is unknown to this AgentScope version` (exit 3) | The database was migrated by a newer AgentScope. Upgrade AgentScope or use another data directory. |
| `schema is not up to date: run agentscope db upgrade` | Shown by `db info`; run `agentscope db upgrade` (other commands upgrade automatically). |
| Provider panel says Ollama is unreachable | Start Ollama or set `AGENTSCOPE_OLLAMA_URL`; or ignore it if you only use the fake model. A missing model is reported with the `ollama pull` command to run yourself. |
| `rows were deleted, but another connection is reading the database` (purge exit 3, API 409) | Another AgentScope process held the SQLite database open. Stop it and purge again to finish erasing. |
| `409 jobs are still running` on purge | Wait for running jobs to finish. |
| A test fails with a socket error | The test tried to reach a non-loopback host; tests must use loopback servers or `httpx.MockTransport`. |
| `npm audit` reports `braces` (GHSA-vfj7-8cjw-p6xm) | A lint-time dev dependency; see [SECURITY.md](../SECURITY.md#known-limitations). `npm audit --omit=dev` is clean. |
