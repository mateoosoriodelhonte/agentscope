"""`agentscope datasets | agents | db | demo | serve`."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner, Result

from agentscope.cli.main import app
from agentscope.config import get_settings
from agentscope.datasets.io import export_dataset
from agentscope.datasets.loader import load_dataset_bytes
from agentscope.datasets.packaged import builtin_datasets
from agentscope.domain.datasets import Dataset, EvalCase, ExpectedAnswer

cli = CliRunner()


def invoke(*args: str | Path) -> Result:
    return cli.invoke(app, [str(a) for a in args])


def dataset_file(tmp_path: Path, description: str = "", **case: Any) -> Path:
    dataset = Dataset(
        name="local",
        description=description,
        agent="research_assistant",
        cases=[
            EvalCase(
                id="footbridge",
                task="In what year did the Meridian Footbridge open?",
                expected=ExpectedAnswer(value="1987"),
                **case,
            )
        ],
    )
    path = tmp_path / f"local{len(description)}.dataset.json"
    path.write_bytes(export_dataset(dataset))
    return path


def test_datasets_list_includes_the_builtins() -> None:
    listed = json.loads(invoke("datasets", "list", "--json").stdout)
    assert {d["name"] for d in listed} >= {d.name for d in builtin_datasets()}
    human = invoke("datasets", "list")
    assert human.exit_code == 0
    assert "research-assistant@1" in human.stdout


def test_datasets_validate(tmp_path: Path) -> None:
    valid = invoke("datasets", "validate", dataset_file(tmp_path), "--json")
    assert valid.exit_code == 0, valid.output
    assert json.loads(valid.stdout)["valid"] is True

    leaky = dataset_file(tmp_path, "x", forbidden_output_substrings=["ghp_" + "a" * 36])
    refused = invoke("datasets", "validate", leaky)
    assert refused.exit_code == 2
    assert "/cases/0/forbidden_output_substrings/0" in refused.stdout
    assert "synthetic canaries" in refused.stdout

    missing = invoke("datasets", "validate", tmp_path / "nope.json", "--json")
    assert missing.exit_code == 2
    assert json.loads(missing.stdout)["valid"] is False


def test_datasets_import_and_export(tmp_path: Path) -> None:
    path = dataset_file(tmp_path)
    imported = invoke("datasets", "import", path, "--json")
    assert imported.exit_code == 0, imported.output
    summary = json.loads(imported.stdout)
    assert (summary["name"], summary["version"]) == ("local", "1")
    assert invoke("datasets", "import", path).exit_code == 0  # identical: no-op

    conflict = invoke("datasets", "import", dataset_file(tmp_path, "changed"))
    assert conflict.exit_code == 2
    assert "already exists" in conflict.output

    out = tmp_path / "exports"
    exported = invoke("datasets", "export", "local@1", "--output", out)
    assert exported.exit_code == 0, exported.output
    written = out / "local@1.dataset.json"
    assert load_dataset_bytes(written.read_bytes()).content_hash() == summary["content_hash"]
    assert invoke("datasets", "export", "local", "--output", out).exit_code == 0
    assert invoke("datasets", "export", "ghost@1", "--output", out).exit_code == 2


def test_datasets_import_refuses_invalid_files(tmp_path: Path) -> None:
    lint_error = dataset_file(tmp_path, "y", expected_tool_calls=[{"tool": "teleport"}])
    result = invoke("datasets", "import", lint_error)
    assert result.exit_code == 2
    assert "teleport" in result.output


def test_agents_list() -> None:
    payload = json.loads(invoke("agents", "list", "--json").stdout)
    refs = {f"{a['name']}@{v['version']}" for a in payload["agents"] for v in a["versions"]}
    assert {"research_assistant@v1", "task_planner@v3"} <= refs
    assert payload["tools"]
    human = invoke("agents", "list")
    assert "research_assistant@v2" in human.stdout


def test_db_upgrade_info_and_purge() -> None:
    upgraded = invoke("db", "upgrade")
    assert upgraded.exit_code == 0, upgraded.output
    assert "database at revision" in upgraded.stdout

    info = json.loads(invoke("db", "info", "--json").stdout)
    assert info["up_to_date"] is True
    assert info["location"].startswith("sqlite:///")
    assert {t["name"] for t in info["storage"]["tables"]} >= {"runs", "datasets"}
    assert "rows" in invoke("db", "info").stdout

    ran = invoke("run", "--task", "Ferry crossing time in minutes?", "--json")
    assert ran.exit_code == 0
    assert invoke("db", "purge", "--scope", "runs").exit_code == 2  # needs --yes
    assert invoke("db", "purge", "--yes", "--before", "yesterday").exit_code == 2
    kept = json.loads(
        invoke("db", "purge", "--yes", "--scope", "runs", "--before", "2000-01-01", "--json").stdout
    )
    assert kept["deleted"]["runs"] == 0
    purged = invoke("db", "purge", "--yes", "--scope", "runs")
    assert purged.exit_code == 0, purged.output
    assert "runs 1" in purged.stdout
    assert json.loads(invoke("traces", "list", "--json").stdout)["total"] == 0


def test_db_info_reports_a_pending_migration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AGENTSCOPE_DATA_DIR", str(tmp_path / "fresh"))
    get_settings.cache_clear()
    result = invoke("db", "info")
    assert result.exit_code == 0
    assert "agentscope db upgrade" in result.stdout


def test_demo_seed_is_idempotent() -> None:
    first = invoke("demo", "seed", "--json")
    assert first.exit_code == 0, first.output
    created = json.loads(first.stdout)
    assert created["created"] == {"suites": 5, "experiments": 2, "runs": 4}
    second = json.loads(invoke("demo", "seed", "--json").stdout)
    assert second["created"] == {"suites": 0, "experiments": 0, "runs": 0}
    assert second["suites"] == created["suites"]
    assert "demo seeded" in invoke("demo", "seed").stdout


class FakeUvicorn:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def run(self, app: Any, **kwargs: Any) -> None:
        self.calls.append({"app": app, **kwargs})


@pytest.fixture
def uvicorn(monkeypatch: pytest.MonkeyPatch) -> FakeUvicorn:
    import uvicorn as real

    fake = FakeUvicorn()
    monkeypatch.setattr(real, "run", fake.run)
    return fake


def test_serve_prints_egress_and_masked_database(uvicorn: FakeUvicorn) -> None:
    result = invoke("serve", "--port", "9123")
    assert result.exit_code == 0, result.output
    assert "http://127.0.0.1:9123" in result.stderr
    assert "ollama: http://127.0.0.1:11434" in result.stderr
    assert "data: sqlite:///" in result.stderr
    assert uvicorn.calls[0]["host"] == "127.0.0.1"
    assert uvicorn.calls[0]["port"] == 9123


def test_database_location_masks_the_password() -> None:
    from agentscope.config import Settings

    settings = Settings(database_url="postgresql://scope:hunter2-secret@db.internal:5432/scope")
    assert settings.database_location == "postgresql://scope:***@db.internal:5432/scope"
    assert "hunter2" not in repr(settings)
    assert "hunter2" not in settings.model_dump_json()


def test_a_database_from_a_newer_release_exits_3(monkeypatch: pytest.MonkeyPatch) -> None:
    from agentscope import services
    from agentscope.storage import db

    def ahead(*_args: object) -> str:
        raise db.DatabaseAheadError("database revision 9999 is unknown to this AgentScope")

    monkeypatch.setattr(db, "upgrade", ahead)
    monkeypatch.setattr(services, "upgrade", ahead)
    for args in (["db", "upgrade"], ["datasets", "list"]):
        result = invoke(*args)
        assert result.exit_code == 3, args
        assert "unknown to this AgentScope" in result.output


def test_serve_remote_mode_needs_a_token(
    uvicorn: FakeUvicorn, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert invoke("serve", "--host", "0.0.0.0").exit_code == 2
    refused = invoke("serve", "--host", "0.0.0.0", "--allow-remote")
    assert refused.exit_code == 2
    assert "AGENTSCOPE_API_TOKEN" in refused.output
    assert not uvicorn.calls

    monkeypatch.setenv("AGENTSCOPE_API_TOKEN", "a-long-remote-token-0123456789")
    get_settings.cache_clear()
    started = invoke(
        "serve", "--host", "0.0.0.0", "--allow-remote", "--allowed-host", "scope.example"
    )
    assert started.exit_code == 0, started.output
    assert "a-long-remote-token" not in started.output
    assert "remote mode" in started.stderr
    assert uvicorn.calls[0]["host"] == "0.0.0.0"
