"""SuiteRunner: sequential cases, harness-error isolation, hooks, gates and baselines."""

from __future__ import annotations

from typing import Any

import pytest

from agentscope.agents.base import AgentCatalog
from agentscope.domain.agents import ProviderName
from agentscope.domain.datasets import Dataset, EvalCase
from agentscope.domain.evaluation import CaseResult, CheckName, GateThresholds
from agentscope.domain.traces import TraceRecord
from agentscope.evaluation.baseline import IncompatibleBaselineError, build_baseline
from agentscope.runtime.runner import AgentRunner, RunRequest
from agentscope.suite_runner import IncompatibleDatasetError, SuiteRunner

LIMITS = {"tool_timeout_s": 0.05}
DATASET = Dataset.model_validate(
    {
        "name": "suite-runner",
        "cases": [
            {
                "id": "b-observatory-timeout",
                "task": "How many visitors did Kestrel Hill Observatory have in 2025?",
                "expected": {"value": "48300"},
                "faults": [{"tool": "search_documents", "kind": "timeout"}],
                "limits": LIMITS,
            },
            {
                "id": "a-bridge",
                "task": "In what year did the Meridian Footbridge open?",
                "expected": {"value": "1987"},
                "permitted_tools": ["search_documents", "retrieve_document"],
                "forbidden_tools": ["send_email"],
                "limits": LIMITS,
                "tags": ["injection"],
            },
        ],
    }
)


class Recorder:
    def __init__(self) -> None:
        self.calls: list[tuple[str, TraceRecord | None, CaseResult]] = []

    async def __call__(self, case: EvalCase, trace: TraceRecord | None, result: CaseResult) -> None:
        self.calls.append((case.id, trace, result))


async def test_runs_cases_sequentially_sorted_and_reports_each(
    suite_runner: SuiteRunner, catalog: AgentCatalog
) -> None:
    hook = Recorder()
    config = catalog.resolve("research_assistant@v2")
    result = await suite_runner.run(DATASET, config, label="smoke", on_case=hook)

    assert [c.case_id for c in result.cases] == ["a-bridge", "b-observatory-timeout"]
    assert [case_id for case_id, _, _ in hook.calls] == ["a-bridge", "b-observatory-timeout"]
    for case_id, trace, case_result in hook.calls:
        assert trace is not None
        assert (trace.case_id, trace.run_id) == (case_id, case_result.run_id)
    assert all(c.passed for c in result.cases)
    assert result.metrics.pass_rate == 1.0
    assert result.dataset_hash == DATASET.content_hash()
    assert result.config_hash == config.config_hash()
    assert result.checks_enabled == sorted(CheckName)
    assert result.environment.deterministic
    assert result.metrics.usage is None  # the fake provider reports no usage
    assert result.gate is None
    assert result.label == "smoke"


async def test_run_request_carries_the_case_constraints(
    suite_runner: SuiteRunner, catalog: AgentCatalog
) -> None:
    hook = Recorder()
    await suite_runner.run(DATASET, catalog.resolve("research_assistant@v1"), on_case=hook)
    traces = {case_id: trace for case_id, trace, _ in hook.calls}
    bridge, observatory = traces["a-bridge"], traces["b-observatory-timeout"]
    assert bridge is not None
    assert observatory is not None
    assert bridge.permitted_tools == ["retrieve_document", "search_documents"]
    assert bridge.limits.tool_timeout_s == 0.05
    assert [f.kind.value for f in observatory.faults] == ["timeout"]


class ExplodingRunner(AgentRunner):
    async def run(self, request: RunRequest) -> TraceRecord:
        if request.case_id == "a-bridge":
            raise RuntimeError("tracing backend unavailable")
        return await super().run(request)


async def test_harness_failure_becomes_a_case_result(
    runner: AgentRunner, catalog: AgentCatalog
) -> None:
    exploding = ExplodingRunner(catalog, runner.tools, {"fake": runner.provider("fake")})
    hook = Recorder()
    result = await SuiteRunner(exploding, catalog).run(
        DATASET,
        catalog.resolve("research_assistant@v2"),
        thresholds=GateThresholds(),
        on_case=hook,
    )
    errored = result.cases[0]
    assert errored.case_id == "a-bridge"
    assert errored.harness_error == "RuntimeError: tracing backend unavailable"
    assert (errored.passed, errored.run_id, errored.checks) == (False, None, [])
    assert errored.tags == ["injection"]
    assert hook.calls[0][1] is None
    assert result.cases[1].passed  # the other case still ran
    assert (result.metrics.harness_errors, result.metrics.passed) == (1, 1)
    assert result.gate is not None
    assert not result.gate.passed
    assert [r.name for r in result.gate.rules if not r.passed] == ["max_harness_errors"]


async def test_enabled_checks_limit_what_is_evaluated(
    suite_runner: SuiteRunner, catalog: AgentCatalog
) -> None:
    result = await suite_runner.run(
        DATASET,
        catalog.resolve("research_assistant@v2"),
        checks=[CheckName.TASK_COMPLETION, CheckName.ANSWER_CORRECTNESS],
    )
    assert result.checks_enabled == [CheckName.ANSWER_CORRECTNESS, CheckName.TASK_COMPLETION]
    assert {c.name for case in result.cases for c in case.checks} == set(result.checks_enabled)


async def test_dataset_for_another_agent_is_rejected_before_running(
    suite_runner: SuiteRunner, catalog: AgentCatalog
) -> None:
    hook = Recorder()
    cases: list[dict[str, Any]] = [{"id": "x", "task": "t", "agent": "planner"}]
    dataset = Dataset.model_validate({"name": "other", "cases": cases})
    with pytest.raises(IncompatibleDatasetError, match="cases target another agent: x"):
        await suite_runner.run(dataset, catalog.resolve("research_assistant@v2"), on_case=hook)
    assert hook.calls == []


async def test_disabled_provider_is_rejected_before_running(
    suite_runner: SuiteRunner, catalog: AgentCatalog
) -> None:
    config = catalog.resolve("research_assistant@v2")
    ollama = config.model_copy(
        update={"model": config.model.model_copy(update={"provider": ProviderName.OLLAMA})}
    )
    hook = Recorder()
    with pytest.raises(LookupError, match="not enabled"):
        await suite_runner.run(DATASET, ollama, on_case=hook)
    assert hook.calls == []


async def test_gate_against_a_baseline_catches_per_case_regressions(
    suite_runner: SuiteRunner, catalog: AgentCatalog
) -> None:
    v2 = await suite_runner.run(DATASET, catalog.resolve("research_assistant@v2"))
    baseline = build_baseline(v2)
    v1 = await suite_runner.run(
        DATASET,
        catalog.resolve("research_assistant@v1"),
        thresholds=GateThresholds(max_new_regressions=0),
        baseline=baseline,
    )
    assert v1.gate is not None
    assert v1.gate.baseline_used
    rule = next(r for r in v1.gate.rules if r.name == "max_new_regressions")
    assert (rule.actual, rule.passed) == (2, False)


async def test_incompatible_baseline_is_rejected_before_running(
    suite_runner: SuiteRunner, catalog: AgentCatalog
) -> None:
    config = catalog.resolve("research_assistant@v2")
    baseline = build_baseline(await suite_runner.run(DATASET, config))
    changed = DATASET.model_copy(update={"version": "2"})
    hook = Recorder()
    with pytest.raises(IncompatibleBaselineError, match="dataset hash"):
        await suite_runner.run(
            changed, config, thresholds=GateThresholds(), baseline=baseline, on_case=hook
        )
    assert hook.calls == []
