# Contributing

Thanks for helping improve AgentScope. This guide covers setup, the checks every change must
pass, commit and pull request conventions, and the common extension tasks. Report security
problems privately as described in [SECURITY.md](SECURITY.md), not in issues.

## Setup

```sh
uv sync --frozen --all-extras
(cd frontend && npm ci)
```

Python 3.12 or later, uv and Node.js 22.12 or later are required. Details, optional services
(PostgreSQL, Ollama) and every setting: [docs/LOCAL_DEVELOPMENT.md](docs/LOCAL_DEVELOPMENT.md).

Dependencies are added only when unavoidable, and must respect the supply-chain cool-off:
uv's `exclude-newer` date in `pyproject.toml`, and `npm install --before=2026-10-09 ...` for
npm. Commit the updated `uv.lock` or `package-lock.json` and explain in the pull request why
the dependency is needed.

## Checks to run

Everything CI runs (`.github/workflows/ci.yml`) must pass locally before you open a pull
request:

```sh
# backend
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run pytest -q

# frontend
cd frontend
npm run lint
npm run type-check
npm test
npx vite build
npm audit --omit=dev --audit-level=high

# quality gate (from the repository root)
uv run agentscope eval --dataset tests/fixtures --agent research_assistant@v2 --check --no-save
uv run agentscope eval --dataset tests/fixtures/planner --agent task_planner@v2 --check --no-save
```

Run the PostgreSQL tests too when you touch storage or migrations
(`AGENTSCOPE_TEST_POSTGRES_URL`, see [docs/LOCAL_DEVELOPMENT.md](docs/LOCAL_DEVELOPMENT.md#postgresql)).
Tests must never use the network: the suite runs under `pytest-socket` with only loopback
hosts allowed; use `httpx.MockTransport` for HTTP clients.

Code conventions: absolute imports only (Ruff bans relative imports), strict mypy, Pydantic
contracts that reject unknown fields, no `v-html` or raw HTML sinks in the dashboard, and no
network, process, filesystem or dynamic-import calls in `agentscope/tools` or
`agentscope/agents` (`tests/unit/test_tool_isolation.py`). `agentscope/evaluation` may import
only the domain contracts, `jsonschema`, `pydantic` and the standard library
(`tests/unit/test_evaluation_isolation.py`).

## Commits and pull requests

- Branch from `main`, named after the issue: `feat/<issue>-<short-name>` or
  `fix/<issue>-<short-name>`.
- Make one or a few meaningful commits. The subject is imperative and at most 72 characters
  ("Add a timeout check for ..."); the body explains what changed and why, wrapped at about 72
  columns.
- Never commit local data or secrets: `.agentscope/`, `*.db`, `.env` and `.env.*` are
  git-ignored, and datasets must hold synthetic canaries, never real credentials.
- Open the pull request with the template in `.github/pull_request_template.md`: what changed,
  why, how it was verified (commands and their results), and anything not done. Every number
  you write in a pull request or in the documentation must come from a command you ran.
- Pull requests are squash-merged; the merge commit subject carries the pull request number,
  as in the existing history (`... (#29)`).
- Update the documentation and `CHANGELOG.md` (under `[Unreleased]`) when behaviour changes.

## Adding a tool

1. Create a `Tool` subclass in `src/agentscope/tools/builtin/` with `name`, `version`,
   `description`, `args_model` (a `ToolArgs` subclass: bounded strings, explicit ASCII patterns
   for identifiers), `output_model` (a `ToolOutput` subclass), `side_effect` and an
   `async def run(self, args, ctx)`. Raise `ToolExecutionError` for failures the agent should see
   as `error`, and `ToolPermissionError` for simulated upstream refusals.
2. Read any data from a JSON file in `src/agentscope/tools/data/` through
   `importlib.resources`, validate it once, and expose it read-only; answer by exact key lookup.
3. Override the fault hooks (`make_incomplete`, `make_misleading`, `inject_instructions`) when
   the defaults (truncate lists, replace or append to the first string leaf) are not realistic.
   Each must return a payload that still validates against `output_model`.
4. Register it in `builtin_tools()` (`src/agentscope/tools/builtin/__init__.py`). A changed
   schema is a new `version` of the same tool, selected by `AgentConfig.tool_schema_version`.
5. Add it to an agent's `toolset` and to the versions that should use it. A write tool must
   only touch the per-run `Sandbox` and should not be added to a built-in toolset.
6. Add unit tests (`tests/unit/test_tools_*.py`), including argument validation and every
   fault hook. Packaged data changes alter the environment fingerprint's data hash.

Details: [docs/TOOL_SECURITY.md](docs/TOOL_SECURITY.md).

## Adding an agent

An agent is an `AgentDefinition` (`src/agentscope/agents/base.py`): toolset, output model,
prompt texts, built-in versions, and a deterministic brain for the fake model.

1. Write `src/agentscope/agents/<name>.py` following `research.py` and `planner.py`: the output
   model with a `status` field, prompts (hardened versions include
   `INSTRUCTION_HIERARCHY_CLAUSE`), the `AgentConfig` versions, and the brain.
2. The brain's `decide(view)` must be a pure function of the `ConversationView`: return
   `tool_call(...)` or `final(...)`, use `obeyed_directives(view)` for the embedded instructions
   a profile follows and `view.resilient` for retry-and-decline behaviour, so the three profiles
   behave as documented.
3. Register the definition in `default_catalog()`.
4. Add graph tests under `tests/graph/` covering each profile, faults and injected
   instructions.
5. Document its trigger-to-decision table in [docs/AGENT_RUNTIME.md](docs/AGENT_RUNTIME.md#the-fake-model).
   If you change an existing brain's decisions, bump `FAKE_BRAIN_VERSION` in
   `src/agentscope/providers/fake.py` (it is part of the effective configuration hash) and
   regenerate the baselines.
6. To gate it in CI: add a built-in dataset in `src/agentscope/datasets/builtin/`, a
   byte-identical copy in its own directory under `tests/fixtures/` with a `gate.json` and
   baselines, and a step in the `quality-gate` job.

## Adding a check

1. Add a member to `CheckName` and any new `ReasonCode`s in
   `src/agentscope/domain/evaluation.py`.
2. Implement `def <name>(ctx: CheckContext) -> CheckResult` in
   `src/agentscope/evaluation/checks.py` and register it in `CHECKS`. Return `not_applicable`
   only when the case sets no expectation the check judges; treat missing data as a failure;
   attach typed evidence with span ids so the dashboard can link it. Do not import anything
   outside `agentscope.domain`, `agentscope.evaluation`, `jsonschema`, `pydantic` and the
   standard library.
3. If the check needs new case fields, add them to `EvalCase` with bounds, add lint rules for
   contradictions (`src/agentscope/datasets/lint.py`), and mirror the contract in
   `frontend/src/api/types.ts` and the dashboard's check labels
   (`frontend/src/components/evaluation/CaseCheckMatrix.vue`).
4. Add scenarios to `tests/evaluation/scenarios.py` so `test_check_coverage_matrix` sees the
   check both pass and fail.
5. Bump `CHECK_ENGINE_VERSION`. Baselines are refused when the enabled checks differ or the
   major version changes, so regenerate them and review the diffs, and revisit the gate
   thresholds.
6. Document applicability and semantics in [docs/EVALUATION.md](docs/EVALUATION.md#checks).

## Regenerating baselines

Baselines are written only by the CLI, never by hand:

```sh
uv run agentscope eval --dataset tests/fixtures --agent research_assistant@v2 \
  --write-baseline tests/fixtures/research_assistant@v2.baseline.json
uv run agentscope eval --dataset tests/fixtures --agent research_assistant@v3 \
  --write-baseline tests/fixtures/research_assistant@v3.baseline.json
uv run agentscope eval --dataset tests/fixtures/planner --agent task_planner@v2 \
  --write-baseline tests/fixtures/planner/task_planner@v2.baseline.json
uv run agentscope eval --dataset tests/fixtures/planner --agent task_planner@v3 \
  --write-baseline tests/fixtures/planner/task_planner@v3.baseline.json
```

Regenerate after a reviewed change to an agent, a brain, a tool, a dataset or a check, then
review the diff: every changed verdict should be explained in the pull request. A baseline is
not written when the suite has harness errors. `tests/unit/test_dataset_builtin.py` fails when
a checked-in baseline no longer matches a fresh run byte for byte. When you change a built-in
dataset, copy it to its fixture directory as well (a test checks that the copies are
identical). See [tests/fixtures/README.md](tests/fixtures/README.md) for why the thresholds
are what they are.

## Regenerating screenshots

The README screenshots in `docs/images/` are generated by Playwright:

```sh
cd frontend
npm run screenshots
```

Regenerate them when a screen they show changes, and review the images before committing.

## Migrations

The schema is managed by Alembic under `src/agentscope/storage/migrations/` (revisions in
`versions/`, numbered `0001`, `0002`, ...). Migrations are applied programmatically by
`agentscope db upgrade` and on startup.

1. Change the ORM models in `src/agentscope/storage/models.py`.
2. Create the next revision. To autogenerate it, upgrade a scratch SQLite database to head and
   run Alembic against it:

   ```python
   # uv run python make_revision.py
   from alembic import command
   from agentscope.storage.db import alembic_config, create_db_engine, upgrade

   url = "sqlite:////tmp/agentscope-migration.db"
   upgrade(create_db_engine(url))
   config = alembic_config()
   config.set_main_option("sqlalchemy.url", url)
   command.revision(config, message="describe the change", autogenerate=True, rev_id="0003")
   ```

   The file is written to `src/agentscope/storage/migrations/versions/`. Review it: migrations
   must import column types only from `agentscope.storage.types` (never from the ORM models),
   SQLite changes use batch mode, and both `upgrade()` and `downgrade()` must work.
3. Run `uv run pytest tests/migrations -q`, which upgrades a fresh database, checks that the
   schema matches the models (no autogenerate drift), downgrades to `0001` and upgrades again,
   and verifies that existing runs and spans survive. Run it against PostgreSQL as well.
4. A database migrated by a newer revision than the code knows is refused
   (`DatabaseAheadError`, exit 3), so never rewrite a released revision; add a new one.
