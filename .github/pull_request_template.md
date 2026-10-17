## What

What this pull request changes. Link the issue it closes (`Closes #...`).

## Why

The problem it solves, and any alternative you rejected.

## Verification

Commands you ran and their results (paste real output; every number must come from a command):

- [ ] `uv run ruff check . && uv run ruff format --check .`
- [ ] `uv run mypy`
- [ ] `uv run pytest -q` (and with `AGENTSCOPE_TEST_POSTGRES_URL` if storage or migrations changed)
- [ ] `cd frontend && npm run lint && npm run type-check && npm test && npx vite build`
- [ ] Quality gate: `uv run agentscope eval --dataset tests/fixtures --agent research_assistant@v2 --check --no-save` and the same for `tests/fixtures/planner` with `task_planner@v2`

## Checklist

- [ ] Baselines regenerated with `--write-baseline` and every changed verdict explained (if agents, brains, tools, datasets or checks changed)
- [ ] `FAKE_BRAIN_VERSION` or `CHECK_ENGINE_VERSION` bumped if brain decisions or check semantics changed
- [ ] New migration added (never an edited released one) if the models changed
- [ ] Documentation and `CHANGELOG.md` (`[Unreleased]`) updated if behaviour changed
- [ ] No secrets, local data (`.agentscope/`, `*.db`, `.env`) or real credentials in the diff
- [ ] No new network destination, stored field or dependency, or it is justified above

## Not done

Anything left out, known limitations, or follow-ups.
