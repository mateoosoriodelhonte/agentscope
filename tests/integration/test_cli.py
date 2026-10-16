from __future__ import annotations

import json

from typer.testing import CliRunner

from agentscope.cli.main import app

cli = CliRunner()


def test_run_json_and_exit_codes() -> None:
    ok = cli.invoke(
        app,
        [
            "run",
            "--agent",
            "research_assistant@v2",
            "--task",
            "In what year did the Meridian Footbridge open?",
            "--expect",
            "1987",
            "--json",
        ],
    )
    assert ok.exit_code == 0, ok.output
    payload = json.loads(ok.stdout)
    assert payload["evaluation"]["passed"] is True
    assert payload["status"] == "completed"

    failed = cli.invoke(
        app,
        [
            "run",
            "--task",
            "In what year did the Meridian Footbridge open?",
            "--expect",
            "1999",
            "--json",
        ],
    )
    assert failed.exit_code == 1

    invalid = cli.invoke(app, ["run", "--agent", "nope", "--task", "x"])
    assert invalid.exit_code == 2

    bad_fault = cli.invoke(app, ["run", "--task", "x", "--fault", "search_documents:explode"])
    assert bad_fault.exit_code == 2


def test_traces_list_and_show() -> None:
    created = cli.invoke(app, ["run", "--task", "Ferry crossing time in minutes?", "--json"])
    run_id = json.loads(created.stdout)["run_id"]
    listing = json.loads(cli.invoke(app, ["traces", "list", "--json"]).stdout)
    assert listing["total"] == 1
    shown = json.loads(cli.invoke(app, ["traces", "show", run_id, "--json"]).stdout)
    assert shown["trace"]["run_id"] == run_id
    assert (
        cli.invoke(app, ["traces", "show", "00000000-0000-0000-0000-000000000000"]).exit_code == 2
    )


def test_serve_refuses_non_loopback_without_flag() -> None:
    result = cli.invoke(app, ["serve", "--host", "0.0.0.0"])
    assert result.exit_code == 2
