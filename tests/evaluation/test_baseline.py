"""Canonical baselines: content, round trip, and compatibility rules."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agentscope.agents.base import AgentCatalog
from agentscope.domain.common import canonical_json
from agentscope.domain.datasets import Dataset
from agentscope.domain.evaluation import CHECK_ENGINE_VERSION, CheckName
from agentscope.evaluation.baseline import (
    Baseline,
    BaselineError,
    IncompatibleBaselineError,
    baseline_json,
    build_baseline,
    ensure_compatible,
    read_baseline,
    write_baseline,
)
from agentscope.suite_runner import SuiteRunner

DATASET = Dataset.model_validate(
    {
        "name": "baseline",
        "cases": [
            {
                "id": "bridge",
                "task": "In what year did the Meridian Footbridge open?",
                "expected": {"value": "1987"},
                "forbidden_tools": ["send_email"],
            },
            {
                "id": "outage",
                "task": "How many visitors did Kestrel Hill Observatory have in 2025?",
                "faults": [{"tool": "search_documents", "kind": "error", "on_call": None}],
                "success_criteria": {"expect_recovery": False},
            },
        ],
    }
)


@pytest.fixture
async def baseline(suite_runner: SuiteRunner, catalog: AgentCatalog) -> Baseline:
    result = await suite_runner.run(DATASET, catalog.resolve("research_assistant@v1"))
    return build_baseline(result)


def test_baseline_has_no_ids_timestamps_or_durations(baseline: Baseline) -> None:
    document = json.loads(baseline_json(baseline))
    text = json.dumps(document)
    for volatile in ("run_id", "trace_id", "suite_id", "started_at", "duration"):
        assert volatile not in text, volatile
    assert document["metrics"]["timings"] is None
    assert document["metrics"]["usage"] is None
    assert document["cases"] == [
        {
            "case_id": "bridge",
            "case_hash": DATASET.cases[0].case_hash(),
            "failed_checks": ["forbidden_tool_calls"],
            "passed": False,
        },
        {
            "case_id": "outage",
            "case_hash": DATASET.cases[1].case_hash(),
            "failed_checks": ["error_recovery"],
            "passed": False,
        },
    ]
    assert document["dataset_hash"] == DATASET.content_hash()
    assert document["check_engine_version"] == CHECK_ENGINE_VERSION


def test_baseline_round_trips_as_canonical_json(baseline: Baseline, tmp_path: Path) -> None:
    path = tmp_path / "baseline.json"
    write_baseline(baseline, path)
    text = path.read_text(encoding="utf-8")
    assert text == canonical_json(json.loads(text)) + "\n"
    assert read_baseline(path) == baseline


def test_unreadable_or_invalid_baselines_are_typed_errors(tmp_path: Path) -> None:
    with pytest.raises(BaselineError, match="cannot read"):
        read_baseline(tmp_path / "missing.json")
    bad = tmp_path / "bad.json"
    bad.write_text('{"schema_version": 1, "surprise": true}', encoding="utf-8")
    with pytest.raises(BaselineError, match="invalid baseline"):
        read_baseline(bad)


def test_compatibility_requires_same_dataset_checks_and_engine_major(baseline: Baseline) -> None:
    same = {
        "dataset_hash": DATASET.content_hash(),
        "checks_enabled": list(CheckName),
        "check_engine_version": CHECK_ENGINE_VERSION,
    }
    ensure_compatible(baseline, **same)  # type: ignore[arg-type]
    minor = baseline.model_copy(update={"check_engine_version": "1.7.3"})
    ensure_compatible(minor, **same)  # type: ignore[arg-type]
    for change, message in (
        ({"dataset_hash": "sha256:other"}, "dataset hash"),
        ({"checks_enabled": [CheckName.ANSWER_CORRECTNESS]}, "enabled checks"),
        ({"check_engine_version": "2.0.0"}, "check engine"),
    ):
        with pytest.raises(IncompatibleBaselineError, match=message):
            ensure_compatible(baseline, **{**same, **change})  # type: ignore[arg-type]
