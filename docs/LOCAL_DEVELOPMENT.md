# Local development

## Prerequisites

| Tool | Version | Needed for |
|---|---|---|
| Python | 3.12 or later (`.python-version` pins 3.12; CI tests 3.12 and 3.13; the Docker image uses 3.13) | everything |
| [uv](https://docs.astral.sh/uv/) | any recent release (the verification in the README used 0.12.21, which the Docker image also pins; CI installs it with `astral-sh/setup-uv`) | the Python environment |
| Node.js | 22.12 or later (`frontend/package.json` engines; CI and the Docker image use 24) | building and testing the dashboard |
| Playwright Chromium | installed with `npx playwright install chromium` | the end-to-end tests and the README screenshots |
| PostgreSQL | optional (CI and Compose use `postgres:18-alpine`) | PostgreSQL storage and its tests |
| Ollama | optional | running agents on a local model |
| Docker with Compose | optional | the container image and the Compose setup |

## Python (uv)

```sh
uv sync --frozen --all-extras    # dev group + extras: postgres (psycopg), otlp (OTLP exporter)
uv run agentscope --help
uv run ruff check .
uv run ruff format --check .     # Python sources and Markdown files
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
npm run test:e2e     # Playwright end-to-end tests (chromium)
npm run screenshots  # regenerates docs/images/*.png
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
`AGENTSCOPE_TEST_POSTGRES_URL` points at a **disposable** database: both groups run
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

### End-to-end tests (Playwright)

```sh
cd frontend
npx playwright install chromium
npm run test:e2e
```

`frontend/playwright.config.ts` builds the dashboard, seeds the demo story into a fresh data
directory (created under the OS temporary directory, or `AGENTSCOPE_E2E_DATA_DIR` when set)
and serves it with `uv run agentscope serve --port 8799`; the specs in `frontend/e2e/` talk
only to `127.0.0.1:8799`. Every other `AGENTSCOPE_*` variable is removed from the server's
environment, so the tests never write into your configured database, and a server already
listening on the port is never reused. The specs cover the demo story, the trace explorer's
evidence links, keyboard-only navigation, dataset import, the New run drawer, settings,
hostile trace content rendered as text, and an axe accessibility scan of the main screens and
the New run drawer in both themes. With `CI` set, as in CI, a failed spec is retried once and
an HTML report is written.

`npm run screenshots` uses the same seeded server to regenerate the README images in
`docs/images/`.

## The Vite dev server and its proxy

`npm run dev` binds `127.0.0.1:5173` (`strictPort`) and proxies `/api` to
`http://127.0.0.1:8765` without rewriting the `Host` header. Start the API first:

```sh
uv run agentscope serve          # terminal 1
cd frontend && npm run dev       # terminal 2, then open http://127.0.0.1:5173
```

The API accepts the proxied requests because the `Host` header (`127.0.0.1:5173`) is a
loopback name. State-changing requests pass the mutation guard because the proxy keeps that
`Host`, so the browser's `Origin` (`http://127.0.0.1:5173`) equals the server's own origin as
the API sees it. `http://127.0.0.1:5173` and `http://localhost:5173` are also the default
`AGENTSCOPE_ALLOWED_ORIGINS`, which matters only for a proxy that rewrites `Host` (Vite's
`changeOrigin: true`); such a proxy on another origin needs that origin added.

## Docker

The `Dockerfile` builds one image with the API and the dashboard: the dashboard is built with
Node 24, AgentScope is installed from `uv.lock` (with the `postgres` extra) into a
Python 3.13 image, and the server runs as the unprivileged user `agentscope` (uid 10001)
with its data in the `/data` volume (`AGENTSCOPE_DATA_DIR=/data`, created with mode `0700`).
A health check polls `/api/health` from inside the container.

With SQLite in a named volume:

```sh
docker build -t agentscope .
docker run --rm -p 127.0.0.1:8765:8765 -v agentscope-data:/data agentscope
```

With PostgreSQL, using Compose:

```sh
cp .env.example .env      # set POSTGRES_PASSWORD in .env (the file is git-ignored)
docker compose up --build
```

`.env.example` suggests `python3 -c 'import secrets; print(secrets.token_urlsafe(32))'` for
the password; a long random value is also what the redactor needs to match it by value
anywhere ([TRACING.md](TRACING.md#redaction-at-write-time)). Compose runs `postgres:18-alpine`
on an internal network with no published port and no route out, and the app with a read-only
root filesystem, a 64 MB `/tmp`, all capabilities dropped and `no-new-privileges`. Ollama is
disabled in this file. To add a local Ollama server:

```sh
docker compose -f docker-compose.yml -f docker-compose.ollama.yml up --build
docker compose -f docker-compose.yml -f docker-compose.ollama.yml exec ollama ollama pull <model>
```

The override enables Ollama at `http://ollama:11434`, allows exactly that host in
`AGENTSCOPE_ALLOWED_EGRESS_HOSTS`, and never publishes Ollama to the host. Pulling a model is a
manual step.

### Container mode

The image runs `agentscope serve --container` (or `AGENTSCOPE_CONTAINER=true`): it binds
`0.0.0.0` inside the container's own network namespace without an API token, while the Host
check still accepts only loopback names. Publish the port on `127.0.0.1` only, as both
examples above do (`-p 127.0.0.1:8765:8765`, `"127.0.0.1:8765:8765"` in Compose). The Host
check stops browsers, not a client that reaches the port directly and sends a loopback
`Host` itself, so publishing on `0.0.0.0` or a LAN address would expose an unauthenticated
API. Container mode refuses `--allowed-host` unless remote mode is on; for network access use
`agentscope serve --container --allow-remote --allowed-host <name>` with
`AGENTSCOPE_API_TOKEN` set ([SECURITY.md](../SECURITY.md#container-mode)).

## The data directory

- `AGENTSCOPE_DATA_DIR` defaults to `.agentscope`, relative to the directory you run
  AgentScope from. Without `AGENTSCOPE_DATABASE_URL`, the database is
  `<data dir>/agentscope.db` (SQLite, WAL mode, so `agentscope.db-wal` and `agentscope.db-shm`
  exist while it is open).
- For SQLite, when the database file's directory does not exist, AgentScope creates it with
  mode `0700` and writes a `.gitignore` containing `*` into it. A directory that already
  exists (a project root, your home directory, `/tmp`) is left exactly as it is. A new
  database file is created with mode `0600`, and SQLite gives its `-wal` and `-shm` files the
  same mode. A location that cannot be created or written fails with `cannot open the
  database at ...: <reason>; choose a writable location with AGENTSCOPE_DATA_DIR or
  AGENTSCOPE_DATABASE_URL` (exit 3). The repository's own `.gitignore` also ignores
  `.agentscope/`, `*.db`, `*.sqlite`, `*.sqlite3`, `.env` and `.env.*` (except
  `.env.example`).
- With `AGENTSCOPE_DATABASE_URL` set (for example
  `postgresql://user:password@127.0.0.1:5432/agentscope`), that database is used instead;
  `postgresql://` and `postgres://` are rewritten to `postgresql+psycopg://`, which needs the
  `postgres` extra.
- Commands that open the engine (`serve`, `run`, `eval`, `compare`, `ablate`, `datasets`,
  `traces`, `agents`, `demo`, `db purge`) upgrade the schema to the latest revision first and
  register the built-in datasets. `agentscope db info` never migrates; it shows the location
  (password masked), the revision and row counts. `agentscope db upgrade` migrates explicitly.
- Work whose process died (a killed `agentscope eval`, a crashed server) is marked
  `interrupted` by the commands that start work (`run`, `eval`, `compare`, `ablate`,
  `demo seed`), by `db upgrade`, and by `agentscope serve` at startup and every minute. Other
  commands never touch it, and work still running in another process is never marked
  ([ARCHITECTURE.md](../ARCHITECTURE.md#liveness-and-recovery)).
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
| `AGENTSCOPE_DATABASE_URL` | unset | SQLAlchemy URL of another database (e.g. PostgreSQL). Secret: masked in `/api/settings` and `db info`, and redacted from traces (its password too, when it is distinctive enough; see [TRACING.md](TRACING.md#redaction-at-write-time)). |
| `AGENTSCOPE_HOST` | `127.0.0.1` | Default bind address for `serve`. A non-loopback value requires remote mode. Ignored in container mode, which binds `0.0.0.0` unless `--host` is given. |
| `AGENTSCOPE_PORT` | `8765` | Default port for `serve` (1 to 65535); also used in the dashboard links the CLI prints. |
| `AGENTSCOPE_ALLOW_REMOTE` | `false` | Remote mode, as `serve --allow-remote`. |
| `AGENTSCOPE_API_TOKEN` | unset | Bearer token required on every request in remote mode (at least 16 characters). Secret. |
| `AGENTSCOPE_ALLOWED_ORIGINS` | `["http://127.0.0.1:5173", "http://localhost:5173"]` | Extra `Origin` values allowed to make state-changing requests (the server's own origin is always allowed). |
| `AGENTSCOPE_MAX_REQUEST_BYTES` | `5242880` (5 MiB) | Body limit for `/api/datasets` requests (at least 1024). Other requests are limited to 256 KiB. The dataset loader refuses documents over 5 MiB whatever this value, so raising it does not admit larger datasets; the dashboard's import dialog checks the same 5 MiB. |
| `AGENTSCOPE_ENABLE_OLLAMA` | `true` | Makes the Ollama provider selectable. Nothing is sent until a run selects it or its status is probed. |
| `AGENTSCOPE_OLLAMA_URL` | `http://127.0.0.1:11434` | Ollama server. A non-loopback host must be allowlisted. |
| `AGENTSCOPE_ALLOWED_EGRESS_HOSTS` | `[]` | Non-loopback hostnames or IP addresses that providers and the OTLP exporter may contact (exact matches). Empty means loopback only. |
| `AGENTSCOPE_ALLOWED_MODEL_HOSTS` | `[]` | Earlier name for the above; still honoured (the two lists are merged). |
| `AGENTSCOPE_ENABLE_OPENAI_COMPATIBLE` | `false` | Enables the OpenAI-compatible provider. |
| `AGENTSCOPE_OPENAI_COMPATIBLE_URL` | unset | Its base URL, e.g. `http://127.0.0.1:8080/v1`. |
| `AGENTSCOPE_OPENAI_COMPATIBLE_API_KEY` | unset | Sent as `Authorization: Bearer`; only over https to non-loopback hosts. Secret. |
| `AGENTSCOPE_OTLP_ENDPOINT` | unset | OTLP/HTTP collector (base URL or `/v1/traces` URL). Unset means no export. |
| `AGENTSCOPE_OTLP_HEADERS` | `{}` | JSON object of headers for the collector; values are secrets. Sent to non-loopback hosts only over https. |
| `AGENTSCOPE_OTLP_EXPORT_CONTENT` | `false` | Also export span payloads (task, prompts, tool arguments and results) and full status descriptions. |
| `AGENTSCOPE_MODEL_RATES` | `{}` | Per-model prices, e.g. `{"llama3.2": {"input_per_1k_tokens": 0.1, "output_per_1k_tokens": 0.2}}`. Costs are shown only for configured models. |
| `AGENTSCOPE_REDACT_KEYS` | `[]` | Additional attribute or field names to redact (exact, case-insensitive). |

Nested values can also be set with the `__` delimiter (for example
`AGENTSCOPE_OTLP_HEADERS__X_TOKEN=...` gives a header named `x_token`), but JSON keeps names
exact.

One variable is read by the CLI rather than by `Settings`: `AGENTSCOPE_CONTAINER=true` is
the same as `agentscope serve --container` (the Docker image passes the flag itself).

Variables read only by tests: `AGENTSCOPE_TEST_POSTGRES_URL`, `AGENTSCOPE_TEST_OLLAMA_MODEL`
and `AGENTSCOPE_TEST_OLLAMA_URL` (pytest), and `AGENTSCOPE_E2E_DATA_DIR` (Playwright).

AgentScope also overrides some third-party variables at startup: `LANGSMITH_TRACING`,
`LANGCHAIN_TRACING_V2` and `LANGCHAIN_TRACING` are forced to `false`; `LANGSMITH_API_KEY`,
`LANGCHAIN_API_KEY`, `LANGSMITH_ENDPOINT`, `LANGCHAIN_ENDPOINT`, `OTEL_EXPORTER_OTLP_*`,
`OTEL_SPAN_*`, `OTEL_ATTRIBUTE_*`, `OTEL_TRACES_EXPORTER` and `OTEL_SDK_DISABLED` are removed
from the process environment, with one warning naming them (never their values).

## CLI reference

| Command | Options |
|---|---|
| `agentscope run` | `--task/-t TEXT` (required, 1 to 4000 characters), `--agent/-a A[@V]` (default `research_assistant`, the latest version), `--fault tool:kind[:on_call]` (repeatable), `--expect VALUE`, `--expect-field PATH` (default `answer`), `--match exact\|normalized\|contains\|numeric` (default `numeric` for number fields, else `normalized`; `one_of` is refused), `--save/--no-save`, `--json` |
| `agentscope eval` | `--dataset/-d PATH\|NAME[@VERSION]` (required), `--agent/-a A@V` or `--config FILE`, `--check`, `--thresholds FILE`, `--baseline FILE`, `--write-baseline FILE`, `--output/-o FILE`, `--json`, `--save/--no-save`, `--label TEXT` |
| `agentscope compare` | `--dataset/-d ...` (required), `--a A@V`, `--b A@V` (required), `--json`, `--output/-o FILE`, `--fail-on-regression` |
| `agentscope ablate` | either `--experiment/-e ID` (a stored experiment, reusing its suites and the stored dataset version it evaluated) or `--dataset/-d PATH\|NAME[@VERSION]` with `--a A@V` and `--b A@V`; `--pairs/--no-pairs` (default: pairs, up to 6 dimensions), `--json`, `--output/-o FILE`, `--save/--no-save`. Exit 0, 2 (invalid input, nothing to ablate) or 3 (a case could not be judged) |
| `agentscope traces list` / `show RUN_ID` | `list`: `--limit N` (1 to 200, default 20), `--agent NAME`, `--include-ablations` (also list runs of ablation variants), `--json`; `show`: `--json` |
| `agentscope datasets list\|validate PATH\|import PATH\|export REF` | `--json` on list, validate and import; `export`: `--output/-o DIR` |
| `agentscope agents list` | `--json` |
| `agentscope db upgrade\|info\|purge` | `info`: `--json`; `purge`: `--scope all\|runs\|evaluations\|experiments\|datasets`, `--before ISO`, `--yes` (required), `--json` |
| `agentscope demo seed` | `--json` |
| `agentscope serve` | `--host`, `--port`, `--allow-remote`, `--allowed-host NAME` (repeatable), `--container` |
| `agentscope version` | |

`--expect` is checked against a field the agent's output really has: `agentscope run -a
task_planner@v3 -t '...' --expect 68 --expect-field total_hours` compares numerically, while
an `--expect` on a field the output lacks exits 2 and lists the fields. `run` validates the
task and faults with the same bounds as `POST /api/runs`, and reports invalid input on one
line with exit 2. Exit codes are listed in the [README](../README.md#cli).

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `400 {"detail": "invalid host header"}` | The `Host` header is not `127.0.0.1`, `localhost` or `::1`. Open the app through one of those, or (remote mode) pass `--allowed-host NAME`. |
| `403 missing X-AgentScope-Client header`, or `must use Content-Type: application/json` | Scripts must send both headers on POST requests: `curl -X POST -H 'Content-Type: application/json' -H 'X-AgentScope-Client: 1' ...`. |
| `403 cross-origin request rejected` / `cross-site request rejected` | The request came from another origin. A dev proxy that rewrites the `Host` header needs its origin in `AGENTSCOPE_ALLOWED_ORIGINS`; a `Sec-Fetch-Site` other than `same-origin` or `none` is always refused. |
| `413 request body exceeds ... bytes` | 256 KiB for most requests; dataset requests up to `AGENTSCOPE_MAX_REQUEST_BYTES`. |
| `429` when starting work | 2 jobs are running and 16 queued; wait for some to finish. |
| `404` at `/api/docs` | There is no interactive API docs page (the CSP blocks Swagger UI's CDN assets); the OpenAPI document is at `/api/openapi.json`. |
| The dashboard does not load at `/` | Build it: `cd frontend && npm run build`, then restart `serve`. |
| `error: refusing to bind 0.0.0.0` (exit 2) | Non-loopback binds need `--allow-remote` and `AGENTSCOPE_API_TOKEN` of at least 16 characters, or `--container` inside a container. |
| `cannot open the database at ...` (exit 3) | The data directory or database file cannot be created or written. Choose a writable `AGENTSCOPE_DATA_DIR` or `AGENTSCOPE_DATABASE_URL`. |
| `database revision ... is unknown to this AgentScope version; upgrade AgentScope` (exit 3) | The database was migrated by a newer AgentScope. Upgrade AgentScope or use another data directory. `db info` says the same. |
| `schema is not up to date: run agentscope db upgrade` | Shown by `db info`; run `agentscope db upgrade` (other commands upgrade automatically). |
| A warning that configured secret values are too short or too simple to redact | A configured key, token or database password is too short or too plain to be matched by value without hitting ordinary words (see [TRACING.md](TRACING.md#redaction-at-write-time)). It is still redacted in URLs and `name=value` text; use a long random value to have it redacted everywhere. |
| Provider panel says Ollama is unreachable | Start Ollama or set `AGENTSCOPE_OLLAMA_URL`; or ignore it if you only use the fake model. A missing model is reported with the `ollama pull` command to run yourself. |
| `eval` or `compare` exits 3 with harness errors `provider_error` | The model provider was unreachable or failed; the cases were not judged and no baseline is written. Check the provider settings and run again. |
| `ablate --experiment ID` exits 2 with `which is not stored` | The experiment's suites evaluated a dataset file that was never imported. Import that exact version (`agentscope datasets import PATH`) and ablate again, or ablate with `--dataset PATH --a A@V --b A@V`. |
| `rows were deleted, but another connection is reading the database` (purge exit 3, API 409 `purge_incomplete`) | Another AgentScope process held the SQLite database open. Stop it and purge again to finish erasing. |
| `409 jobs are still running; nothing was deleted` on purge (`jobs_running`) | Wait for running jobs to finish, then purge. |
| A test fails with a socket error | The test tried to reach a non-loopback host; tests must use loopback servers or `httpx.MockTransport`. |
| `npm audit` reports `braces` (GHSA-vfj7-8cjw-p6xm) | A lint-time dev dependency; see [SECURITY.md](../SECURITY.md#known-limitations). `npm audit --omit=dev` is clean. |
