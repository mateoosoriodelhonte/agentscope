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


def test_run_rejects_invalid_input_as_the_api_does() -> None:
    """Bad input is exit 2 with a short message, never 'internal error' (exit 3), and the
    task bounds are those POST /api/runs enforces."""
    for args in (
        ["--task", "", "--expect", "x"],
        ["--task", "x" * 4001, "--expect", "x"],
        ["--task", ""],
        ["--task", "x" * 4001],
        ["--task", "x", "--fault", "search_documents:timeout:0"],
        ["--task", "x", "--expect-field", "answer"],
    ):
        result = cli.invoke(app, ["run", *args])
        assert result.exit_code == 2, (args, result.output)
        assert "internal error" not in result.output
        assert "errors.pydantic.dev" not in result.output
        assert len(result.output.strip().splitlines()) == 1, result.output


def test_run_expectations_follow_the_agents_output() -> None:
    planner = [
        "run",
        "--agent",
        "task_planner@v3",
        "--task",
        "Estimate the total hours for project prj-ticket-api.",
        "--json",
    ]
    no_answer = cli.invoke(app, [*planner, "--expect", "68"])
    assert no_answer.exit_code == 2  # task_planner has no `answer`: say so, do not "fail"
    assert "total_hours" in no_answer.output

    numeric = cli.invoke(app, [*planner, "--expect", "68", "--expect-field", "total_hours"])
    assert numeric.exit_code == 0, numeric.output
    expected = json.loads(numeric.stdout)["evaluation"]
    assert expected["passed"] is True

    wrong = cli.invoke(app, [*planner, "--expect", "67", "--expect-field", "total_hours"])
    assert wrong.exit_code == 1
    not_a_number = cli.invoke(app, [*planner, "--expect", "lots", "--expect-field", "total_hours"])
    assert not_a_number.exit_code == 2
    exact = cli.invoke(
        app,
        [
            "run",
            "--task",
            "In what year did the Meridian Footbridge open?",
            "--expect",
            "1987",
            "--match",
            "exact",
        ],
    )
    assert exact.exit_code == 0, exact.output


def test_simulated_runs_say_so() -> None:
    created = cli.invoke(app, ["run", "--task", "Ferry crossing time in minutes?"])
    assert created.exit_code == 0, created.output
    assert "Simulated model" in created.stdout
    payload = json.loads(cli.invoke(app, ["run", "--task", "Ferry time?", "--json"]).stdout)
    assert payload["simulated"] is True
    shown = cli.invoke(app, ["traces", "show", payload["run_id"]])
    assert "Simulated model" in shown.stdout
    shown_json = json.loads(cli.invoke(app, ["traces", "show", payload["run_id"], "--json"]).stdout)
    assert shown_json["simulated"] is True
