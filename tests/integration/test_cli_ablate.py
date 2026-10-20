"""`agentscope ablate`: exit codes, the one JSON document, and the human-readable report."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner, Result

from agentscope.cli.main import app
from agentscope.datasets.io import export_dataset
from agentscope.domain.datasets import Dataset

cli = CliRunner()
V1, V2 = "research_assistant@v1", "research_assistant@v2"


def invoke(*args: str | Path) -> Result:
    return cli.invoke(app, [str(a) for a in args], terminal_width=200)


def document(result: Result) -> dict[str, Any]:
    payload: dict[str, Any] = json.loads(result.stdout)
    assert set(payload) == {"ablation", "exit_code"}
    assert payload["exit_code"] == result.exit_code
    return payload


@pytest.fixture
def small(tmp_path: Path, small_research: Dataset) -> Path:
    path = tmp_path / "small.dataset.json"
    path.write_bytes(export_dataset(small_research))
    return path


def test_json_is_one_document_and_no_pairs_skips_them(small: Path) -> None:
    result = invoke("ablate", "-d", small, "--a", V1, "--b", V2, "--no-pairs", "--json")
    assert result.exit_code == 0, result.output
    ablation = document(result)["ablation"]
    assert (ablation["a"]["agent_ref"], ablation["b"]["agent_ref"]) == (V1, V2)
    assert (ablation["pairs"], ablation["pairs_skipped"]) == ([], True)
    assert len(ablation["variants"]) == 10
    assert ablation["n"] == 3
    listing = json.loads(invoke("traces", "list", "--json", "--limit", "200").stdout)
    assert listing["total"] == 12 * 3  # every suite was saved, variants included


def test_text_report(small: Path) -> None:
    result = invoke("ablate", "--dataset", small, "--a", V1, "--b", V2)
    assert result.exit_code == 0, result.output
    shown = " ".join(result.stdout.split())  # however the terminal wrapped it
    assert "Simulated model" in shown
    assert "3 cases compared" in shown
    for heading in ("dimension", "alone from A", "removed from B", "gained/lost"):
        assert heading in shown
    assert "isolation + prompt" in shown  # the one pair that interacts
    assert "total +100.0 pp = sum of solo effects +66.7 pp + interaction +33.3 pp" in shown
    assert "injection-encoded-hex" in shown
    assert "(together:" in shown  # a narrow terminal wraps the rest inside the table cell
    assert shown.endswith("saved")


def test_an_experiment_is_ablated_with_its_suites(small: Path, tmp_path: Path) -> None:
    compared = invoke("compare", "-d", small, "--a", V1, "--b", V2, "--json")
    assert compared.exit_code == 0, compared.output
    comparison = json.loads(compared.stdout)["comparison"]
    experiment = comparison["experiment_id"]
    # Its dataset came from a file: the version it evaluated must be stored to be reused.
    unstored = invoke("ablate", "--experiment", experiment)
    assert unstored.exit_code == 2
    assert "agentscope datasets import" in " ".join(unstored.output.split())
    assert invoke("datasets", "import", small).exit_code == 0

    output = tmp_path / "ablation.json"
    result = invoke("ablate", "--experiment", experiment, "--no-pairs", "-o", output, "--json")
    assert result.exit_code == 0, result.output
    ablation = document(result)["ablation"]
    assert ablation["experiment_id"] == comparison["experiment_id"]
    assert ablation["a"]["suite_id"] == comparison["a"]["suite_id"]
    assert json.loads(output.read_text()) == document(result)


def test_no_save_stores_nothing(small: Path) -> None:
    result = invoke("ablate", "-d", small, "--a", V1, "--b", V2, "--no-pairs", "--no-save")
    assert result.exit_code == 0, result.output
    assert "not saved (--no-save)" in result.stdout
    assert json.loads(invoke("traces", "list", "--json").stdout)["total"] == 0


@pytest.mark.parametrize(
    ("args", "message"),
    [
        ([], "pass --experiment ID, or --dataset with --a and --b"),
        (["--a", V1, "--b", V2], "pass --experiment ID"),
        (["--experiment", "x", "--a", V1], "use either --experiment"),
        (["--experiment", "00000000-0000-0000-0000-000000000000"], "not found"),
        (["--dataset", "missing@1", "--a", V1, "--b", V2], "no stored dataset missing@1"),
        (["--dataset", "research-assistant", "--a", V1, "--b", "nobody@v1"], "nobody"),
        (["--dataset", "research-assistant", "--a", V1, "--b", V1], "nothing to ablate"),
        (["--dataset", "research-assistant", "--a", V1, "--b", V2, "-o", "/nope/x.json"], "-o"),
    ],
)
def test_invalid_input_exits_2(args: list[str], message: str) -> None:
    result = invoke("ablate", *args)
    assert result.exit_code == 2, result.output
    assert message in result.output.replace("\n", " ")


def test_harness_errors_exit_3(small: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from agentscope.providers.base import ProviderError
    from agentscope.providers.fake import FakeModelProvider

    async def refuse(*_args: object, **_kwargs: object) -> None:
        raise ProviderError("connection refused")

    monkeypatch.setattr(FakeModelProvider, "generate", refuse)
    result = invoke("ablate", "-d", small, "--a", V1, "--b", V2, "--no-pairs", "--json")
    assert result.exit_code == 3
    ablation = document(result)["ablation"]
    assert ablation["a"]["metrics"]["harness_errors"] == 3
    assert ablation["n"] == 0


def test_unexpected_exceptions_exit_3(small: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from agentscope.services import Services

    async def explode(*_args: object, **_kwargs: object) -> None:
        raise OSError("disk on fire")

    monkeypatch.setattr(Services, "run_suite", explode)
    result = invoke("ablate", "-d", small, "--a", V1, "--b", V2)
    assert result.exit_code == 3
    assert "ablation failed: OSError: disk on fire" in result.output
