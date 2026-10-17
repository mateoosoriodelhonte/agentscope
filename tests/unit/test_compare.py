"""Suite comparison: case classification, flips, exact deltas and config attribution."""

from __future__ import annotations

from datetime import UTC, datetime
from fractions import Fraction
from typing import Any

import pytest

from agentscope.agents.research import VERSIONS
from agentscope.domain.agents import AgentConfig
from agentscope.domain.evaluation import (
    CHECK_ENGINE_VERSION,
    CaseResult,
    CheckName,
    CheckResult,
    CheckStatus,
    EnvironmentFingerprint,
    ReasonCode,
    ResolvedContent,
    SuiteResult,
)
from agentscope.domain.experiments import CaseChange, CheckFlip, Comparison
from agentscope.evaluation.metrics import compute_metrics
from agentscope.experiments.compare import compare, config_diff

T0 = datetime(2026, 1, 1, tzinfo=UTC)
V1, V2, V3 = VERSIONS
ANSWER, POLICY = CheckName.ANSWER_CORRECTNESS, CheckName.POLICY_COMPLIANCE


def case(case_id: str, answer: str, policy: str, case_hash: str | None = None) -> CaseResult:
    checks = [
        CheckResult(name=name, status=CheckStatus(status), reason=ReasonCode.OK, message="")
        for name, status in ((ANSWER, answer), (POLICY, policy))
    ]
    return CaseResult(
        case_id=case_id,
        case_hash=case_hash or f"sha256:{case_id}",
        run_id=f"run-{case_id}-{answer}-{policy}",
        trace_id=None,
        passed=all(c.status is not CheckStatus.FAILED for c in checks),
        status=None,
        checks=checks,
        steps=3,
    )


def suite(
    config: AgentConfig,
    cases: list[CaseResult],
    dataset_hash: str = "sha256:dataset",
    effective: str | None = None,
    deterministic: bool = True,
) -> SuiteResult:
    return SuiteResult(
        suite_id=f"suite-{config.version}",
        dataset_name="compare",
        dataset_version="1",
        dataset_hash=dataset_hash,
        agent_config=config,
        config_hash=config.config_hash(),
        effective_config_hash=effective or f"sha256:{config.version}",
        checks_enabled=sorted(CheckName),
        environment=EnvironmentFingerprint(
            agentscope_version="0.1.0",
            check_engine_version=CHECK_ENGINE_VERSION,
            python_version="3.12.0",
            langgraph_version="1.2.0",
            data_hash="sha256:data",
            provider="fake" if deterministic else "ollama",
            model=config.model.model,
            deterministic=deterministic,
        ),
        started_at=T0,
        finished_at=T0,
        cases=cases,
        metrics=compute_metrics(cases),
    )


P, F = "passed", "failed"
A = suite(
    V1,
    [
        case("c1", P, P),
        case("c2", P, P),
        case("c3", F, P),
        case("c4", F, P),
        case("c5", F, P),
        case("c6", P, P),
        case("c7", P, P),
        case("c9", F, P),
    ],
    dataset_hash="sha256:a",
)
B = suite(
    V2,
    [
        case("c1", P, P),
        case("c2", P, F),
        case("c3", P, P),
        case("c4", F, P),
        case("c5", F, F),
        case("c6", P, F, case_hash="sha256:c6-edited"),
        case("c8", P, P),
        case("c9", P, P),
    ],
    dataset_hash="sha256:b",
)


def test_cases_are_classified_by_id_and_content() -> None:
    result = compare(A, B, "v1 vs v2")
    assert {c.case_id: c.change for c in result.cases} == {
        "c1": CaseChange.STILL_PASSING,
        "c2": CaseChange.REGRESSION,
        "c3": CaseChange.FIX,
        "c4": CaseChange.STILL_FAILING_SAME,
        "c5": CaseChange.STILL_FAILING_CHANGED,
        "c6": CaseChange.CONTENT_CHANGED,
        "c7": CaseChange.REMOVED,
        "c8": CaseChange.ADDED,
        "c9": CaseChange.FIX,
    }
    c5 = next(c for c in result.cases if c.case_id == "c5")
    assert (c5.a_failed_checks, c5.b_failed_checks) == (
        ["answer_correctness"],
        ["answer_correctness", "policy_compliance"],
    )
    added = next(c for c in result.cases if c.case_id == "c8")
    assert (added.a_passed, added.b_passed, added.a_run_id) == (None, True, None)
    assert result.dataset_mismatch


def test_flips_exclude_changed_added_and_removed_cases() -> None:
    result = compare(A, B, "v1 vs v2")
    # c6 also regressed on policy, but its content changed, so it is not counted.
    assert result.check_flips == [
        CheckFlip(check="answer_correctness", regressions=0, fixes=2),
        CheckFlip(check="policy_compliance", regressions=2, fixes=0),
    ]


def test_metric_deltas_are_exact_points_and_counts_over_compared_cases() -> None:
    deltas = {d.metric: d for d in compare(A, B, "v1 vs v2").metric_deltas}
    pass_rate = deltas["pass_rate"]
    assert (pass_rate.unit, pass_rate.n) == ("pp", 6)
    assert pass_rate.a == float(Fraction(200, 6))
    assert pass_rate.b == 50.0
    assert pass_rate.delta == float(Fraction(50, 3))
    assert pass_rate.improved is True
    assert (deltas["passed"].a, deltas["passed"].b, deltas["passed"].delta) == (2, 3, 1)
    violations = deltas["policy_violation_cases"]
    assert (violations.unit, violations.delta, violations.improved) == ("count", 2, False)
    assert deltas["check:answer_correctness"].delta == float(Fraction(100, 3))
    assert deltas["check:policy_compliance"].delta == -float(Fraction(100, 3))
    assert (deltas["avg_steps"].unit, deltas["avg_steps"].delta) == ("steps", 0)
    assert deltas["avg_steps"].improved is None


def test_v1_to_v2_is_confounded() -> None:
    result = compare(A, B, "v1 vs v2")
    assert [(d.path, d.dimension) for d in result.config_diff] == [
        ("graph", "graph"),
        ("isolate_untrusted", "isolation"),
        ("prompt_version", "prompt"),
        ("retry.max_decision_repairs", "retry"),
        ("retry.max_output_repairs", "retry"),
        ("retry.max_tool_retries", "retry"),
        ("tool_schema_version", "tool_schema"),
    ]
    assert result.confounded
    assert result.deterministic
    assert (result.a.agent_ref, result.b.agent_ref) == (
        "research_assistant@v1",
        "research_assistant@v2",
    )


def test_single_dimension_change_is_attributable() -> None:
    diff = config_diff(V2, V3)
    assert [(d.path, d.dimension, d.a, d.b) for d in diff] == [
        ("model.model", "model", "scripted-naive", "scripted-careful")
    ]
    same = [case("c1", P, P)]
    result = compare(suite(V2, same), suite(V3, same), "model only")
    assert not result.confounded
    assert not result.dataset_mismatch
    assert [c.change for c in result.cases] == [CaseChange.STILL_PASSING]


def test_hidden_changes_surface_through_the_effective_hash() -> None:
    same = [case("c1", P, P)]
    result = compare(
        suite(V2, same, effective="sha256:old"), suite(V2, same, effective="sha256:new"), "same"
    )
    assert [(d.path, d.dimension) for d in result.config_diff] == [
        ("effective_config_hash", "other")
    ]
    assert not result.confounded


def test_a_non_deterministic_side_makes_the_comparison_non_deterministic() -> None:
    same = [case("c1", P, P)]
    result = compare(
        suite(V2, same),
        suite(V2, same, deterministic=False),
        "noisy",
        experiment_id="exp-1",
        created_at=T0,
    )
    assert not result.deterministic
    assert (result.experiment_id, result.created_at) == ("exp-1", T0)


# --- resolved content and the environment ------------------------------------------------

SAME = [case("c1", P, P)]


def resolved(**changes: Any) -> ResolvedContent:
    base: dict[str, Any] = {
        "prompt": "sha256:prompt",
        "tools": {"search_documents": "sha256:search", "retrieve_document": "sha256:read"},
        "output_schema": "sha256:schema",
        "graph": "sha256:graph",
        "fake_brain_version": "3",
    }
    return ResolvedContent.model_validate({**base, **changes})


def with_environment(result: SuiteResult, **changes: Any) -> SuiteResult:
    return result.model_copy(update={"environment": result.environment.model_copy(update=changes)})


def dimensions(result: Comparison) -> list[tuple[str, str]]:
    return [(d.path, d.dimension) for d in result.config_diff]


def test_a_hidden_prompt_change_next_to_a_model_change_is_confounded() -> None:
    a = with_environment(suite(V2, SAME), resolved=resolved())
    b = with_environment(suite(V3, SAME), resolved=resolved(prompt="sha256:edited"))
    result = compare(a, b, "model and hidden prompt")
    assert dimensions(result) == [("model.model", "model"), ("resolved.prompt", "prompt")]
    assert result.confounded


@pytest.mark.parametrize(
    ("changes", "expected"),
    [
        (
            {"tools": {"search_documents": "sha256:v2", "retrieve_document": "sha256:read"}},
            ("resolved.tools.search_documents", "tool_schema"),
        ),
        ({"output_schema": "sha256:other"}, ("resolved.output_schema", "other")),
        ({"graph": "sha256:rewired"}, ("resolved.graph", "graph")),
        ({"fake_brain_version": "4"}, ("resolved.fake_brain_version", "model")),
    ],
)
def test_each_hidden_component_is_its_own_dimension(
    changes: dict[str, Any], expected: tuple[str, str]
) -> None:
    a = with_environment(suite(V2, SAME), resolved=resolved())
    b = with_environment(suite(V3, SAME), resolved=resolved(**changes))
    result = compare(a, b, "hidden")
    assert dimensions(result) == [("model.model", "model"), expected]
    assert result.confounded == (expected[1] != "model")


def test_a_hidden_change_alone_is_attributable() -> None:
    a = with_environment(suite(V2, SAME), resolved=resolved())
    b = with_environment(
        suite(V2, SAME, effective="sha256:edited"), resolved=resolved(prompt="sha256:edited")
    )
    result = compare(a, b, "prompt text only")
    assert dimensions(result) == [("resolved.prompt", "prompt")]
    assert not result.confounded


def test_changes_the_configuration_explains_are_not_listed_twice() -> None:
    """A new prompt version has new prompt text, and a removed tool has no schema."""
    narrowed = V2.model_copy(update={"prompt_version": "p1", "tools": ["search_documents"]})
    a = with_environment(suite(V2, SAME), resolved=resolved())
    b = with_environment(
        suite(narrowed, SAME),
        resolved=resolved(prompt="sha256:p1", tools={"search_documents": "sha256:search"}),
    )
    assert dimensions(compare(a, b, "explained")) == [
        ("prompt_version", "prompt"),
        ("tools", "tools"),
    ]


def test_a_tool_data_change_is_reported() -> None:
    a = suite(V2, SAME)
    b = with_environment(suite(V3, SAME), data_hash="sha256:new-data")
    result = compare(a, b, "data")
    assert dimensions(result) == [("model.model", "model"), ("environment.data_hash", "other")]
    assert result.confounded


def test_an_agentscope_upgrade_is_reported() -> None:
    """Without per-component hashes an upgrade can still have changed prompts or tools."""
    a = suite(V2, SAME)
    b = with_environment(suite(V3, SAME), agentscope_version="0.2.0")
    result = compare(a, b, "upgrade")
    assert ("environment.agentscope_version", "other") in dimensions(result)
    assert result.confounded


def test_a_check_engine_mismatch_is_a_warning() -> None:
    a = suite(V2, SAME)
    b = with_environment(suite(V2, SAME), check_engine_version="2.0.0")
    result = compare(a, b, "engine")
    assert any("check engine" in w for w in result.warnings)
    assert dimensions(result) == []


def test_unrecorded_resolved_content_is_a_warning_when_configurations_differ() -> None:
    result = compare(suite(V2, SAME), suite(V3, SAME), "legacy")
    assert any("resolved content" in w for w in result.warnings)
    recorded = compare(
        with_environment(suite(V2, SAME), resolved=resolved()),
        with_environment(suite(V3, SAME), resolved=resolved()),
        "recorded",
    )
    assert recorded.warnings == []
    assert dimensions(recorded) == [("model.model", "model")]


# --- harness errors are not regressions --------------------------------------------------


def errored(case_id: str) -> CaseResult:
    return CaseResult(
        case_id=case_id,
        case_hash=f"sha256:{case_id}",
        run_id=None,
        trace_id=None,
        passed=False,
        status=None,
        harness_error="internal_error: boom",
    )


def test_a_harness_error_is_neither_a_regression_nor_a_fix() -> None:
    a = suite(V2, [case("c1", P, P), errored("c2")])
    b = suite(V2, [errored("c1"), case("c2", P, P)])
    result = compare(a, b, "harness")
    assert {c.case_id: c.change for c in result.cases} == {
        "c1": CaseChange.HARNESS_ERROR,
        "c2": CaseChange.HARNESS_ERROR,
    }
    assert all(f.regressions == 0 and f.fixes == 0 for f in result.check_flips)
    harness = next(d for d in result.metric_deltas if d.metric == "harness_errors")
    assert (harness.a, harness.b) == (1, 1)
