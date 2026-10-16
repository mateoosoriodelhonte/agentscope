"""Packaged datasets and the CI quality-gate fixtures.

The behaviour matrix runs every case of the research dataset against research_assistant
v1, v2 and v3 under the deterministic fake model, so each case is known to discriminate the
way its title says. Verdicts are pinned for the checks that exist today; injection
containment is asserted directly on the traces so it does not depend on the check engine.
"""

from __future__ import annotations

import asyncio
import json
from fractions import Fraction
from importlib import resources
from pathlib import Path

import pytest

from agentscope.agents.base import AgentCatalog, default_catalog
from agentscope.datasets.lint import lint
from agentscope.datasets.loader import GATE_FILE, load_dataset_dir, load_gate_file
from agentscope.datasets.packaged import (
    UnknownDatasetError,
    builtin_datasets,
    load_builtin_dataset,
)
from agentscope.domain.datasets import Dataset, EvalCase
from agentscope.domain.evaluation import CheckName
from agentscope.domain.traces import TraceRecord
from agentscope.evaluation.engine import evaluate_case
from agentscope.providers.fake import FakeModelProvider
from agentscope.runtime.runner import AgentRunner, RunRequest
from agentscope.tools.registry import ToolRegistry, default_registry

FIXTURES = Path(__file__).parents[1] / "fixtures"
RESEARCH = "research-assistant"
VERSIONS = ("v1", "v2", "v3")
# The checks the evaluation engine ships today; pinned so new checks do not move this matrix.
CURRENT_CHECKS = [
    CheckName.ANSWER_CORRECTNESS,
    CheckName.SCHEMA_VALIDITY,
    CheckName.REQUIRED_TOOL_CALLS,
    CheckName.TASK_COMPLETION,
]
# v2's naive model does not retry malformed output, read past incomplete data, or decline;
# no version has a decision-repair budget yet, so model-invalid-json fails everywhere.
EXPECTED_FAILURES = {
    "v1": {
        "qa-transit-fare",
        "fault-timeout-transient",
        "fault-error-persistent",
        "fault-malformed-search",
        "fault-incomplete-retrieve",
        "fault-misleading-conflict",
        "model-invalid-json",
        "model-schema-violation",
        "unanswerable-lighthouse",
    },
    "v2": {
        "fault-error-persistent",
        "fault-malformed-search",
        "fault-incomplete-retrieve",
        "fault-misleading-conflict",
        "model-invalid-json",
        "unanswerable-lighthouse",
    },
    "v3": {"model-invalid-json"},
}


def test_builtin_datasets_are_listed_and_loadable() -> None:
    names = [d.name for d in builtin_datasets()]
    assert RESEARCH in names
    assert names == sorted(names)
    research = load_builtin_dataset(RESEARCH)
    assert research.agent == "research_assistant"
    assert 14 <= len(research.cases) <= 20
    with pytest.raises(UnknownDatasetError, match="available: "):
        load_builtin_dataset("nope")


def _lint_targets(catalog: AgentCatalog) -> list[tuple[Dataset, str | None]]:
    """Every packaged dataset, linted without a config and against each of its agent's versions."""
    targets: list[tuple[Dataset, str | None]] = []
    for dataset in builtin_datasets():
        targets.append((dataset, None))
        assert dataset.agent is not None
        targets += [(dataset, v.ref) for v in catalog.get(dataset.agent).versions]
    return targets


def test_builtin_datasets_lint_clean(catalog: AgentCatalog, registry: ToolRegistry) -> None:
    for dataset, ref in _lint_targets(catalog):
        config = catalog.resolve(ref) if ref else None
        assert lint(dataset, catalog, registry, config=config) == [], (dataset.name, ref)


def test_fixture_dataset_is_the_builtin_research_dataset() -> None:
    builtin = resources.files("agentscope.datasets.builtin").joinpath("research_assistant.json")
    fixture = FIXTURES / "research-assistant.dataset.json"
    assert fixture.read_bytes() == builtin.read_bytes(), "copy the built-in dataset over"
    loaded = load_dataset_dir(FIXTURES)
    assert loaded == load_builtin_dataset(RESEARCH)
    assert loaded.content_hash() == load_builtin_dataset(RESEARCH).content_hash()


def test_fixture_gate_is_valid_and_names_existing_checks() -> None:
    gate = load_gate_file(FIXTURES / GATE_FILE)
    assert gate.min_pass_rate is not None
    assert set(gate.required_checks) <= set(CheckName)


def test_cases_cover_the_documented_scenarios() -> None:
    tags = {tag for case in load_builtin_dataset(RESEARCH).cases for tag in case.tags}
    assert {
        "qa",
        "tool-use",
        "failure-injection",
        "timeout",
        "error",
        "malformed",
        "incomplete",
        "misleading",
        "prompt-injection",
        "corpus-injection",
        "model-fault",
        "unanswerable",
    } <= tags


# --- behaviour under research_assistant v1, v2, v3 ------------------------------------------


def _run_all(dataset: Dataset) -> dict[tuple[str, str], TraceRecord]:
    catalog = default_catalog()
    runner = AgentRunner(catalog, default_registry(), {"fake": FakeModelProvider(catalog.brains())})

    async def run(case: EvalCase, version: str) -> TraceRecord:
        return await runner.run(
            RunRequest(
                config=catalog.resolve(f"research_assistant@{version}"),
                task=case.task,
                faults=case.faults,
                model_faults=case.model_faults,
                permitted_tools=case.permitted_tools,
                forbidden_tools=case.forbidden_tools,
                limits=case.limits,
                case_id=case.id,
            )
        )

    async def everything() -> dict[tuple[str, str], TraceRecord]:
        return {
            (case.id, version): await run(case, version)
            for case in dataset.cases
            for version in VERSIONS
        }

    return asyncio.run(everything())


@pytest.fixture(scope="module")
def research() -> Dataset:
    return load_builtin_dataset(RESEARCH)


@pytest.fixture(scope="module")
def traces(research: Dataset) -> dict[tuple[str, str], TraceRecord]:
    return _run_all(research)


@pytest.mark.parametrize("version", VERSIONS)
def test_each_version_fails_exactly_the_expected_cases(
    research: Dataset, traces: dict[tuple[str, str], TraceRecord], version: str
) -> None:
    schema = default_catalog().get("research_assistant").output_schema()
    failed = {
        case.id
        for case in research.cases
        if not evaluate_case(case, traces[case.id, version], schema, CURRENT_CHECKS).passed
    }
    assert failed == EXPECTED_FAILURES[version]


def _violations(case: EvalCase, trace: TraceRecord) -> list[str]:
    attempted = [
        f"attempted {i.tool}" for i in trace.tool_invocations if i.tool in case.forbidden_tools
    ]
    output = json.dumps(trace.final_output, ensure_ascii=False)
    leaked = [f"output contains {s}" for s in case.forbidden_output_substrings if s in output]
    return attempted + leaked


def test_injections_compromise_v1_only_and_are_always_contained(
    research: Dataset, traces: dict[tuple[str, str], TraceRecord]
) -> None:
    guarded = [c for c in research.cases if c.forbidden_tools or c.forbidden_output_substrings]
    assert len(guarded) >= 4
    for case in guarded:
        assert _violations(case, traces[case.id, "v1"]), case.id
        assert _violations(case, traces[case.id, "v2"]) == [], case.id
        assert _violations(case, traces[case.id, "v3"]) == [], case.id
    for trace in traces.values():
        assert all(i.tool in trace.permitted_tools for i in trace.tool_invocations if i.executed)


def test_the_provisional_gate_admits_v2_and_rejects_v1(
    research: Dataset, traces: dict[tuple[str, str], TraceRecord]
) -> None:
    """The thresholds in tests/fixtures/gate.json are calibrated on real results, not guessed:
    research_assistant@v2 meets every rule the current engine can judge, v1 does not."""
    gate = load_gate_file(FIXTURES / GATE_FILE)
    assert gate.min_pass_rate is not None
    assert gate.max_invalid_outputs is not None
    schema = default_catalog().get("research_assistant").output_schema()

    def meets_gate(version: str) -> bool:
        results = [
            evaluate_case(case, traces[case.id, version], schema, CURRENT_CHECKS)
            for case in research.cases
        ]
        passed = sum(r.passed for r in results)
        policy_cases = sum(bool(_violations(c, traces[c.id, version])) for c in research.cases)
        return (
            Fraction(passed, len(results)) >= Fraction(gate.min_pass_rate)
            and sum(r.invalid_output for r in results) <= gate.max_invalid_outputs
            and policy_cases <= (gate.max_policy_violation_cases or 0)
            and sum(r.unauthorized_tool_executions for r in results)
            <= (gate.max_unauthorized_tool_executions or 0)
        )

    assert meets_gate("v2")
    assert not meets_gate("v1")
