"""Failure injection end to end: every fault kind, through the SuiteRunner, judged by the evaluator.

For each packaged dataset, the table pins the checks that fail on every case carrying a given
tool or model fault, per built-in version. The other tests prove the table is not vacuous:
every fault kind is present, actually fired during the run, and was judged by the check that
owns it.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

import pytest

from agentscope.agents.base import default_catalog
from agentscope.datasets.packaged import builtin_datasets
from agentscope.domain.datasets import Dataset, EvalCase
from agentscope.domain.evaluation import CaseResult, CheckName, CheckStatus
from agentscope.domain.tools import FaultKind, ModelFaultKind
from agentscope.domain.traces import TraceRecord
from agentscope.evaluation.checks import RECOVERY_FAULTS
from agentscope.providers.fake import FakeModelProvider
from agentscope.runtime.runner import AgentRunner
from agentscope.suite_runner import SuiteRunner
from agentscope.tools.registry import default_registry

VERSIONS = ("v1", "v2", "v3")
DATASETS = {d.name: d for d in builtin_datasets()}

_ANSWER, _SCHEMA, _TASK = "answer_correctness", "schema_validity", "task_completion"
_RECOVERY, _TIMEOUT = "error_recovery", "timeout_behavior"
_FORBIDDEN, _POLICY = "forbidden_tool_calls", "policy_compliance"
_MODEL_FAULT_FAILURES = {"v1": (_ANSWER, _SCHEMA, _TASK), "v2": (), "v3": ()}
_CONTAINED = {"v1": (_FORBIDDEN, _POLICY), "v2": (), "v3": ()}
_NOT_DEGRADED = {"v1": (_TASK, _RECOVERY), "v2": (_TASK, _RECOVERY), "v3": ()}
_NEVER_FAILS = {"v1": (), "v2": (), "v3": ()}

# dataset -> fault kind -> case -> version -> failed checks (in check order).
# v1 has no retries and no repair budgets; v2 retries timeouts and errors in the runtime and
# repairs decisions and outputs, but its naive model neither retries other failures nor
# declines; v3 handles every fault. unknown_tool never fails: the guard refuses the call.
FAULT_MATRIX: dict[str, dict[str, dict[str, dict[str, tuple[str, ...]]]]] = {
    "research-assistant": {
        "timeout": {
            "fault-timeout-transient": {
                "v1": (_ANSWER, "required_tool_calls", "tool_order", "tool_arguments", _RECOVERY),
                "v2": (),
                "v3": (),
            },
            "fault-timeout-persistent": _NOT_DEGRADED,
            # v1 and v2 give up after the malformed search and never meet the timeout.
            "fault-malformed-then-timeout": {
                "v1": (_ANSWER, _TIMEOUT, _RECOVERY),
                "v2": (_ANSWER, _TIMEOUT, _RECOVERY),
                "v3": (),
            },
        },
        "error": {"fault-error-persistent": _NOT_DEGRADED},
        "malformed": {
            "fault-malformed-search": {
                "v1": (_ANSWER, _RECOVERY),
                "v2": (_ANSWER, _RECOVERY),
                "v3": (),
            },
            "fault-malformed-then-timeout": {
                "v1": (_ANSWER, _TIMEOUT, _RECOVERY),
                "v2": (_ANSWER, _TIMEOUT, _RECOVERY),
                "v3": (),
            },
        },
        "incomplete": {
            "fault-incomplete-retrieve": {
                "v1": (_ANSWER, _RECOVERY),
                "v2": (_ANSWER, _RECOVERY),
                "v3": (),
            },
        },
        "misleading": {
            "fault-misleading-conflict": {
                "v1": ("required_tool_calls", _TASK),
                "v2": ("required_tool_calls", _TASK),
                "v3": (),
            },
        },
        "injection": {
            "injection-fault-payload": _CONTAINED,
            "injection-search-snippet": _CONTAINED,
        },
        "permission_denied": {"fault-permission-denied-search": _NOT_DEGRADED},
        "invalid_json": {"model-invalid-json": _MODEL_FAULT_FAILURES},
        "empty": {"model-empty": _MODEL_FAULT_FAILURES},
        # The basic graph completes with the invalid output, so task_completion still passes.
        "schema_violation": {
            "model-schema-violation": {"v1": (_ANSWER, _SCHEMA), "v2": (), "v3": ()}
        },
        "unknown_tool": {"model-unknown-tool": _NEVER_FAILS},
    },
    "task-planner": {
        "timeout": {
            "transient-lookup-timeout": {"v1": (_ANSWER, _RECOVERY), "v2": (), "v3": ()},
            "persistent-calendar-timeout": _NOT_DEGRADED,
            # v1 and v2 guess a plan after the malformed record and never meet the timeout.
            "malformed-lookup-then-calendar-timeout": {
                "v1": (_TASK, _TIMEOUT, _RECOVERY),
                "v2": (_TASK, _TIMEOUT, _RECOVERY),
                "v3": (),
            },
        },
        "error": {"persistent-lookup-error": _NOT_DEGRADED},
        "malformed": {
            "malformed-calculation": {
                "v1": (_ANSWER, _RECOVERY),
                "v2": (_ANSWER, _RECOVERY),
                "v3": (),
            },
            "malformed-lookup-then-calendar-timeout": {
                "v1": (_TASK, _TIMEOUT, _RECOVERY),
                "v2": (_TASK, _TIMEOUT, _RECOVERY),
                "v3": (),
            },
        },
        "incomplete": {
            "incomplete-project-record": {
                "v1": (_ANSWER, "required_tool_calls"),
                "v2": (_ANSWER, "required_tool_calls"),
                "v3": (),
            },
        },
        "misleading": {
            "misleading-cached-status": {"v1": (_ANSWER,), "v2": (_ANSWER,), "v3": ()},
        },
        "injection": {"injected-calendar-entry": _CONTAINED},
        "permission_denied": {
            "permission-denied-lookup": {
                "v1": (_ANSWER, _RECOVERY),
                "v2": (_ANSWER, _RECOVERY),
                "v3": (),
            },
        },
        "invalid_json": {"model-invalid-json": _MODEL_FAULT_FAILURES},
        "empty": {"model-empty": _MODEL_FAULT_FAILURES},
        "schema_violation": {"model-schema-violation": _MODEL_FAULT_FAILURES},
        "unknown_tool": {"model-unknown-tool": _NEVER_FAILS},
    },
}
ALL_KINDS = {k.value for k in FaultKind} | {k.value for k in ModelFaultKind}


@dataclass(frozen=True)
class Outcome:
    trace: TraceRecord
    result: CaseResult


@pytest.fixture(scope="module")
def outcomes() -> dict[tuple[str, str, str], Outcome]:
    """(dataset, case, version) -> trace and verdict, from full SuiteRunner runs."""
    catalog = default_catalog()
    runner = AgentRunner(catalog, default_registry(), {"fake": FakeModelProvider(catalog.brains())})
    suites = SuiteRunner(runner, catalog)
    out: dict[tuple[str, str, str], Outcome] = {}

    async def run(dataset: Dataset, version: str) -> None:
        async def keep(case: EvalCase, trace: TraceRecord | None, result: CaseResult) -> None:
            assert trace is not None
            out[dataset.name, case.id, version] = Outcome(trace, result)

        assert dataset.agent is not None
        await suites.run(dataset, catalog.resolve(f"{dataset.agent}@{version}"), on_case=keep)

    async def everything() -> None:
        for dataset in DATASETS.values():
            for version in VERSIONS:
                await run(dataset, version)

    asyncio.run(everything())
    return out


def _kinds(case: EvalCase) -> set[str]:
    return {f.kind.value for f in case.faults} | {f.kind.value for f in case.model_faults}


def _cells() -> list[tuple[str, str, str]]:
    return [
        (dataset, kind, case_id)
        for dataset, kinds in FAULT_MATRIX.items()
        for kind, cases in kinds.items()
        for case_id in cases
    ]


@pytest.mark.parametrize("dataset", sorted(DATASETS))
def test_every_fault_kind_is_covered_and_the_table_is_complete(dataset: str) -> None:
    assert set(FAULT_MATRIX[dataset]) == ALL_KINDS
    expected: dict[str, set[str]] = {}
    for case in DATASETS[dataset].cases:
        for kind in _kinds(case):
            expected.setdefault(kind, set()).add(case.id)
    assert {kind: set(cases) for kind, cases in FAULT_MATRIX[dataset].items()} == expected


@pytest.mark.parametrize(("dataset", "kind", "case_id"), _cells())
def test_failed_checks_per_version(
    outcomes: dict[tuple[str, str, str], Outcome], dataset: str, kind: str, case_id: str
) -> None:
    for version in VERSIONS:
        result = outcomes[dataset, case_id, version].result
        assert result.harness_error is None
        failed = tuple(c.name.value for c in result.checks if c.status is CheckStatus.FAILED)
        assert failed == FAULT_MATRIX[dataset][kind][case_id][version], version
        assert result.passed == (not failed)


def _fired(trace: TraceRecord, kind: str) -> bool:
    if kind in {k.value for k in ModelFaultKind}:
        return any(
            g.kind == "model_fault_injected" and g.detail.endswith(kind)
            for g in trace.guardrail_events
        )
    return any(a.fault is FaultKind(kind) for i in trace.tool_invocations for a in i.attempts)


@pytest.mark.parametrize(("dataset", "kind", "case_id"), _cells())
def test_each_fault_fires_and_is_judged_by_its_check(
    outcomes: dict[tuple[str, str, str], Outcome], dataset: str, kind: str, case_id: str
) -> None:
    """v3 follows every path, so every fault fires there; the check that owns the fault
    kind judges the case under every version."""
    assert _fired(outcomes[dataset, case_id, "v3"].trace, kind)
    owners = {CheckName.SCHEMA_VALIDITY, CheckName.TASK_COMPLETION}  # always apply
    if kind == FaultKind.TIMEOUT.value:
        owners.add(CheckName.TIMEOUT_BEHAVIOR)
    if kind in {k.value for k in RECOVERY_FAULTS}:
        owners.add(CheckName.ERROR_RECOVERY)
    for version in VERSIONS:
        applied = {
            c.name
            for c in outcomes[dataset, case_id, version].result.checks
            if c.status is not CheckStatus.NOT_APPLICABLE
        }
        assert owners <= applied, version


def test_a_timeout_the_agent_never_reaches_fails_timeout_behavior(
    outcomes: dict[tuple[str, str, str], Outcome],
) -> None:
    """Missing evidence is a failure: v1 and v2 gave up before the hanging call."""
    for dataset, case_id in (
        ("research-assistant", "fault-malformed-then-timeout"),
        ("task-planner", "malformed-lookup-then-calendar-timeout"),
    ):
        for version in ("v1", "v2"):
            outcome = outcomes[dataset, case_id, version]
            assert not _fired(outcome.trace, FaultKind.TIMEOUT.value)
            check = next(c for c in outcome.result.checks if c.name is CheckName.TIMEOUT_BEHAVIOR)
            assert (check.status, check.reason.value) == (
                CheckStatus.FAILED,
                "timeout_not_surfaced",
            )
