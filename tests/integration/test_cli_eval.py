"""`agentscope eval` and `agentscope compare`: exit codes, gating inputs, and output shape.

Datasets are written to temporary directories from the packaged research dataset, so these
tests do not depend on the CI fixtures in tests/fixtures.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner, Result

from agentscope.cli.main import app
from agentscope.datasets.io import export_dataset
from agentscope.datasets.packaged import load_builtin_dataset
from agentscope.domain.datasets import Dataset, EvalCase, ExpectedAnswer

cli = CliRunner()
RESEARCH = load_builtin_dataset("research-assistant")


def invoke(*args: str | Path) -> Result:
    return cli.invoke(app, [str(a) for a in args])


def document(result: Result) -> dict[str, Any]:
    payload: dict[str, Any] = json.loads(result.stdout)
    assert set(payload) == {"suite", "gate", "exit_code"}
    assert payload["exit_code"] == result.exit_code
    return payload


def suite_dir(tmp_path: Path, gate: dict[str, Any] | None = None) -> Path:
    directory = tmp_path / "suite"
    directory.mkdir()
    (directory / "research.dataset.json").write_bytes(export_dataset(RESEARCH))
    if gate is not None:
        (directory / "gate.json").write_text(json.dumps(gate), encoding="utf-8")
    return directory


def single_case_file(tmp_path: Path, expected: str = "1987") -> Path:
    dataset = Dataset(
        name="single",
        agent="research_assistant",
        cases=[
            EvalCase(
                id="footbridge",
                task="In what year did the Meridian Footbridge open?",
                expected=ExpectedAnswer(value=expected),
            )
        ],
    )
    path = tmp_path / "single.dataset.json"
    path.write_bytes(export_dataset(dataset))
    return path


def test_check_passes_and_prints_exactly_one_json_document(tmp_path: Path) -> None:
    directory = suite_dir(tmp_path, {"min_pass_rate": 0.5, "max_policy_violation_cases": 0})
    result = invoke("eval", "--dataset", directory, "--check", "--no-save", "--json")
    assert result.exit_code == 0, result.output
    payload = document(result)
    assert payload["suite"]["agent_config"]["version"] == "v3"  # latest built-in by default
    assert len(payload["suite"]["cases"]) == len(RESEARCH.cases)
    assert payload["gate"]["passed"] is True
    assert payload["gate"] == payload["suite"]["gate"]
    assert {r["name"] for r in payload["gate"]["rules"]} >= {"min_pass_rate"}


def test_failed_gate_exits_1(tmp_path: Path) -> None:
    directory = suite_dir(tmp_path, {"min_pass_rate": 0.9})
    result = invoke(
        "eval",
        "--dataset",
        directory,
        "--agent",
        "research_assistant@v1",
        "--check",
        "--json",
        "--no-save",
    )
    assert result.exit_code == 1
    payload = document(result)
    assert payload["gate"]["passed"] is False


def test_check_without_thresholds_requires_every_case_to_pass(tmp_path: Path) -> None:
    passing = invoke("eval", "--dataset", single_case_file(tmp_path), "--check", "--no-save")
    assert passing.exit_code == 0, passing.output
    failing = tmp_path / "failing"
    failing.mkdir()
    result = invoke(
        "eval", "--dataset", single_case_file(failing, "1999"), "--check", "--json", "--no-save"
    )
    assert result.exit_code == 1
    rules = {r["name"]: r for r in document(result)["gate"]["rules"]}
    assert rules["min_pass_rate"]["threshold"] == 1.0
    assert rules["min_pass_rate"]["actual"] == "0/1"


def test_without_check_failures_are_reported_not_gated(tmp_path: Path) -> None:
    result = invoke(
        "eval",
        "--dataset",
        suite_dir(tmp_path, {"min_pass_rate": 1.0}),
        "--agent",
        "research_assistant@v1",
        "--json",
        "--no-save",
    )
    assert result.exit_code == 0
    payload = document(result)
    assert payload["gate"] is None  # gate.json is only read with --check
    assert payload["suite"]["metrics"]["passed"] < payload["suite"]["metrics"]["total"]


def test_baseline_written_then_found_by_convention(tmp_path: Path) -> None:
    directory = suite_dir(tmp_path, {"max_new_regressions": 0})
    baseline = directory / "research_assistant@v2.baseline.json"
    written = invoke(
        "eval",
        "--dataset",
        directory,
        "--agent",
        "research_assistant@v2",
        "--no-save",
        "--write-baseline",
        baseline,
    )
    assert written.exit_code == 0, written.output
    first = baseline.read_bytes()

    gated = invoke(
        "eval",
        "--dataset",
        directory,
        "--agent",
        "research_assistant@v2",
        "--check",
        "--json",
        "--no-save",
    )
    assert gated.exit_code == 0, gated.output
    gate = document(gated)["gate"]
    assert gate["baseline_used"] is True
    assert gate["rules"][0] == {
        "name": "max_new_regressions",
        "threshold": 0,
        "actual": 0,
        "passed": True,
        "detail": "",
    }

    again = invoke(
        "eval",
        "--dataset",
        directory,
        "--agent",
        "research_assistant@v2",
        "--no-save",
        "--write-baseline",
        baseline,
    )
    assert again.exit_code == 0
    assert baseline.read_bytes() == first  # deterministic: no ids, timestamps or durations

    # A baseline recorded on other dataset content is not comparable: invalid input.
    incompatible = invoke(
        "eval",
        "--dataset",
        single_case_file(tmp_path),
        "--agent",
        "research_assistant@v2",
        "--baseline",
        baseline,
        "--no-save",
    )
    assert incompatible.exit_code == 2
    assert "not comparable" in incompatible.output


def test_missing_baseline_fails_a_regression_rule(tmp_path: Path) -> None:
    directory = suite_dir(tmp_path, {"max_new_regressions": 0})
    result = invoke("eval", "--dataset", directory, "--check", "--json", "--no-save")
    assert result.exit_code == 1
    rule = document(result)["gate"]["rules"][0]
    assert rule["detail"] == "requires a baseline"


def test_gate_symlink_escaping_the_directory_is_ignored(tmp_path: Path) -> None:
    directory = suite_dir(tmp_path)
    lenient = tmp_path / "lenient.json"
    lenient.write_text(json.dumps({"min_pass_rate": 0.0}), encoding="utf-8")
    (directory / "gate.json").symlink_to(lenient)
    result = invoke(
        "eval",
        "--dataset",
        directory,
        "--agent",
        "research_assistant@v1",
        "--check",
        "--json",
        "--no-save",
    )
    # The lenient gate was skipped, so every case had to pass.
    assert result.exit_code == 1
    assert "ignoring" in result.stderr
    assert document(result)["gate"]["rules"][0]["threshold"] == 1.0


def test_baseline_symlink_escaping_the_directory_is_ignored(tmp_path: Path) -> None:
    directory = suite_dir(tmp_path, {"max_new_regressions": 0})
    outside = tmp_path / "outside.baseline.json"
    written = invoke(
        "eval",
        "--dataset",
        directory,
        "--agent",
        "research_assistant@v2",
        "--no-save",
        "--write-baseline",
        outside,
    )
    assert written.exit_code == 0, written.output
    (directory / "research_assistant@v2.baseline.json").symlink_to(outside)
    result = invoke(
        "eval",
        "--dataset",
        directory,
        "--agent",
        "research_assistant@v2",
        "--check",
        "--json",
        "--no-save",
    )
    # The baseline outside the directory was skipped, so the regression rule cannot pass.
    assert result.exit_code == 1
    assert "ignoring" in result.stderr
    assert document(result)["gate"]["rules"][0]["detail"] == "requires a baseline"


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (["--dataset", "no-such-dataset"], "no stored dataset"),
        (["--dataset", "research-assistant@99"], "no stored dataset"),
        (["--dataset", "research-assistant", "--agent", "ghost"], "unknown agent"),
        (["--dataset", "research-assistant", "--agent", "task_planner@v2"], "invalid dataset"),
        (
            ["--dataset", "research-assistant", "--agent", "research_assistant", "--config", "x"],
            "not both",
        ),
        (["--dataset", "research-assistant", "--thresholds", "missing.json"], "cannot open"),
    ],
)
def test_invalid_input_exits_2(args: list[str], message: str) -> None:
    result = invoke("eval", *args, "--no-save")
    assert result.exit_code == 2, result.output
    assert message in result.output


def test_invalid_dataset_file_exits_2_with_located_issues(tmp_path: Path) -> None:
    bad = tmp_path / "bad.dataset.json"
    bad.write_text('{"name": "x", "name": "y", "cases": []}', encoding="utf-8")
    result = invoke("eval", "--dataset", bad)
    assert result.exit_code == 2
    assert "/name: duplicate key" in result.output


def test_config_file_selects_the_agent(tmp_path: Path) -> None:
    from agentscope.agents.base import default_catalog

    config = default_catalog().resolve("research_assistant@v2")
    path = tmp_path / "agent.json"
    path.write_text(config.model_copy(update={"version": "mine"}).model_dump_json())
    result = invoke("eval", "--dataset", single_case_file(tmp_path), "--config", path, "--json")
    assert result.exit_code == 0, result.output
    assert document(result)["suite"]["agent_config"]["version"] == "mine"
    path.write_text('{"agent": "research_assistant"}')
    assert invoke("eval", "--dataset", "research-assistant", "--config", path).exit_code == 2


def test_harness_errors_exit_3(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from agentscope.runtime.runner import AgentRunner

    async def crash(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("simulated harness defect")

    monkeypatch.setattr(AgentRunner, "run", crash)
    result = invoke(
        "eval",
        "--dataset",
        single_case_file(tmp_path),
        "--json",
        "--write-baseline",
        tmp_path / "never.json",
    )
    assert result.exit_code == 3
    payload = document(result)
    assert payload["suite"]["cases"][0]["harness_error"].startswith("RuntimeError")
    assert not (tmp_path / "never.json").exists()


def test_unexpected_exceptions_exit_3(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from agentscope.services import Services

    async def explode(*_args: object, **_kwargs: object) -> None:
        raise OSError("disk on fire")

    monkeypatch.setattr(Services, "run_suite", explode)
    result = invoke("eval", "--dataset", single_case_file(tmp_path))
    assert result.exit_code == 3
    assert "OSError: disk on fire" in result.output


def test_saved_eval_prints_deep_links_and_is_stored(tmp_path: Path) -> None:
    output = tmp_path / "result.json"
    result = invoke(
        "eval",
        "--dataset",
        "research-assistant@1",
        "--agent",
        "research_assistant@v2",
        "--label",
        "local",
        "--output",
        output,
    )
    assert result.exit_code == 0, result.output
    saved = json.loads(output.read_text())
    suite_id = saved["suite"]["suite_id"]
    failed = [c for c in saved["suite"]["cases"] if not c["passed"]]
    assert failed
    for case in failed:
        assert f"http://127.0.0.1:8765/runs/{case['run_id']}?check=" in result.stdout
    assert f"evaluation {suite_id} saved" in result.stdout
    assert "Simulated model" in result.stdout
    listing = json.loads(invoke("traces", "list", "--json", "--limit", "200").stdout)
    assert listing["total"] == len(RESEARCH.cases)


def test_compare(tmp_path: Path) -> None:
    result = invoke(
        "compare",
        "--dataset",
        "research-assistant",
        "--a",
        "research_assistant@v1",
        "--b",
        "research_assistant@v2",
        "--json",
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert set(payload) == {"comparison", "exit_code"}
    comparison = payload["comparison"]
    assert comparison["a"]["agent_ref"] == "research_assistant@v1"
    assert comparison["confounded"] is True
    assert any(c["change"] == "fix" for c in comparison["cases"])

    reverse = invoke(
        "compare",
        "--dataset",
        "research-assistant",
        "--a",
        "research_assistant@v2",
        "--b",
        "research_assistant@v1",
        "--fail-on-regression",
    )
    assert reverse.exit_code == 1
    assert "regression" in reverse.stdout
    assert "confounded: no single change can be credited" in reverse.stdout
    assert "agentscope ablate" in reverse.stdout

    unknown = invoke("compare", "--dataset", "research-assistant", "--a", "x", "--b", "y")
    assert unknown.exit_code == 2


@pytest.fixture
def provider_down(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every model call fails as if the provider were unreachable (no network involved)."""
    from agentscope.providers.base import ProviderError
    from agentscope.providers.fake import FakeModelProvider

    async def refuse(*_args: object, **_kwargs: object) -> None:
        raise ProviderError("connection refused: http://127.0.0.1:9/v1")

    monkeypatch.setattr(FakeModelProvider, "generate", refuse)


@pytest.mark.usefixtures("provider_down")
def test_provider_failures_are_harness_errors_not_agent_failures(tmp_path: Path) -> None:
    """An outage says nothing about the agent: exit 3 (with or without --check), kept out of
    verdicts and invalid outputs, and never written into a baseline."""
    baseline = tmp_path / "outage.baseline.json"
    for extra in ([], ["--check"]):
        result = invoke(
            "eval",
            "--dataset",
            single_case_file(tmp_path),
            "--json",
            "--no-save",
            "--write-baseline",
            baseline,
            *extra,
        )
        assert result.exit_code == 3, result.output
        suite = document(result)["suite"]
        assert suite["cases"][0]["harness_error"].startswith("provider_error: ")
        assert suite["cases"][0]["checks"] == []
        assert suite["metrics"]["harness_errors"] == 1
        assert suite["metrics"]["invalid_outputs"] == 0
        assert not baseline.exists()


@pytest.mark.usefixtures("provider_down")
def test_a_provider_failure_in_an_adhoc_run_exits_3() -> None:
    for extra in ([], ["--expect", "1987"]):
        result = cli.invoke(
            app, ["run", "--task", "When did the Meridian Footbridge open?", "--json", *extra]
        )
        assert result.exit_code == 3, result.output
        payload = json.loads(result.stdout)
        assert payload["error_code"] == "provider_error"
        assert payload["exit_code"] == 3
        if extra:
            assert payload["evaluation"]["harness_error"].startswith("provider_error")


def test_a_baseline_alone_gates_on_new_regressions(tmp_path: Path) -> None:
    baseline = tmp_path / "v2.baseline.json"
    directory = suite_dir(tmp_path)
    written = invoke(
        "eval",
        "--dataset",
        directory,
        "-a",
        "research_assistant@v2",
        "--no-save",
        "--write-baseline",
        baseline,
    )
    assert written.exit_code == 0, written.output
    result = invoke(
        "eval",
        "--dataset",
        directory,
        "-a",
        "research_assistant@v1",
        "--no-save",
        "--json",
        "--baseline",
        baseline,
    )
    assert result.exit_code == 1, result.output
    gate = document(result)["gate"]
    assert gate["baseline_used"] is True
    regressions = next(r for r in gate["rules"] if r["name"] == "max_new_regressions")
    assert regressions["actual"] > 0
    assert regressions["detail"].startswith("regressed: ")


@pytest.mark.parametrize("option", ["--write-baseline", "--output"])
def test_unwritable_destinations_are_refused_before_running(
    tmp_path: Path, option: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from agentscope.services import Services

    async def never(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("the suite must not run")

    monkeypatch.setattr(Services, "run_suite", never)
    for target in (tmp_path / "nodir" / "out.json", tmp_path):
        result = invoke("eval", "--dataset", single_case_file(tmp_path), option, target, "--json")
        assert result.exit_code == 2, result.output
        assert option in result.output
        assert result.stdout == ""


def test_a_failed_write_after_the_run_keeps_the_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from agentscope.cli import evaluate

    def vanished(path: Path, _payload: object) -> None:
        raise FileNotFoundError(2, "No such file or directory", str(path))

    monkeypatch.setattr(evaluate, "write_json", vanished)
    output = tmp_path / "out.json"
    result = invoke("eval", "--dataset", single_case_file(tmp_path), "-o", output, "--json")
    assert result.exit_code == 3
    assert document(result)["suite"]["metrics"]["passed"] == 1  # printed, not lost
    assert "could not write" in result.stderr


def test_help_shows_how_to_pin_a_dataset_version() -> None:
    for args in (["eval", "--help"], ["compare", "--help"], ["datasets", "export", "--help"]):
        result = cli.invoke(app, args, terminal_width=200)
        assert result.exit_code == 0
        assert "NAME@VERSION" in result.output, args


def test_a_comparison_with_one_simulated_side_says_so(capsys: pytest.CaptureFixture[str]) -> None:
    from agentscope.cli.evaluate import _render_comparison
    from agentscope.domain.experiments import Comparison

    result = invoke(
        "compare",
        "--dataset",
        "research-assistant",
        "--a",
        "research_assistant@v1",
        "--b",
        "research_assistant@v2",
        "--json",
    )
    capsys.readouterr()
    # As if B had run on a real model: the comparison is not deterministic, A still simulated.
    comparison = Comparison.model_validate(json.loads(result.stdout)["comparison"])
    _render_comparison(comparison.model_copy(update={"deterministic": False}), ["A"])
    shown = capsys.readouterr().out
    assert "A: Simulated model" in shown
    assert "flips may be noise" in shown


def test_compare_text_output_groups_every_case_change() -> None:
    """Every change except still_passing (summarized as a count) gets its own group, so no
    case silently disappears from the human-readable comparison."""
    from agentscope.cli.evaluate import _CHANGE_ORDER
    from agentscope.domain.experiments import CaseChange

    assert set(_CHANGE_ORDER) | {"still_passing"} == {c.value for c in CaseChange}
