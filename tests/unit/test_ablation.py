"""Ablation planning and attribution on hand-built suite results: exact effects, pair
interactions, per-case attribution, and what cannot be compared or ablated."""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import UTC, datetime
from fractions import Fraction

import pytest

from agentscope.agents.research import VERSIONS
from agentscope.domain.ablations import AblationDirection
from agentscope.domain.agents import AgentConfig
from agentscope.domain.evaluation import (
    CHECK_ENGINE_VERSION,
    CaseResult,
    CheckName,
    EnvironmentFingerprint,
    SuiteResult,
)
from agentscope.domain.experiments import CaseChange
from agentscope.evaluation.metrics import compute_metrics
from agentscope.experiments.ablation import (
    Variant,
    ablatable_dimensions,
    attribute,
    plan_variants,
)

T0 = datetime(2026, 1, 1, tzinfo=UTC)
V1, V2, _ = VERSIONS
SLUG = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
# B changes two dimensions of A: the prompt version and untrusted-content isolation.
A = V1
B = V1.model_copy(update={"version": "v1b", "prompt_version": "p2", "isolate_untrusted": True})


def environment(**changes: object) -> EnvironmentFingerprint:
    fields: dict[str, object] = {
        "agentscope_version": "1.0.0",
        "check_engine_version": CHECK_ENGINE_VERSION,
        "python_version": "3.12.0",
        "langgraph_version": "1.2.0",
        "data_hash": "sha256:data",
        "provider": "fake",
        "model": "scripted-naive",
        "deterministic": True,
    }
    return EnvironmentFingerprint.model_validate({**fields, **changes})


def suite(
    config: AgentConfig,
    verdicts: Mapping[str, bool | None],
    *,
    hashes: Mapping[str, str] | None = None,
    env: EnvironmentFingerprint | None = None,
) -> SuiteResult:
    """One case per verdict; None is a harness error (no run, no verdict)."""
    cases = [
        CaseResult(
            case_id=case_id,
            case_hash=(hashes or {}).get(case_id, f"sha256:{case_id}"),
            run_id=None if passed is None else f"run-{config.version}-{case_id}",
            trace_id=None,
            passed=bool(passed),
            status=None,
            harness_error="RuntimeError: boom" if passed is None else None,
        )
        for case_id, passed in verdicts.items()
    ]
    return SuiteResult(
        suite_id=f"suite-{config.version}",
        dataset_name="unit",
        dataset_version="1",
        dataset_hash="sha256:dataset",
        agent_config=config,
        config_hash=config.config_hash(),
        effective_config_hash=f"sha256:{config.version}",
        checks_enabled=sorted(CheckName),
        environment=env or environment(),
        started_at=T0,
        finished_at=T0,
        cases=cases,
        metrics=compute_metrics(cases),
    )


def verdicts(row: str) -> dict[str, bool | None]:
    """'TF-x' -> c1 passes, c2 fails, c3 has a harness error, c4 ... (x: no such case)."""
    symbols = {"T": True, "F": False, "-": None}
    return {f"c{i}": symbols[s] for i, s in enumerate(row, 1) if s != "x"}


def by_key(variants: list[Variant]) -> dict[str, Variant]:
    return {variant.key: variant for variant in variants}


# --- planning -------------------------------------------------------------------------


def test_ablatable_dimensions_are_top_level_fields_in_configuration_order() -> None:
    assert ablatable_dimensions(V1, V2) == {
        "graph": ("graph",),
        "isolation": ("isolate_untrusted",),
        "prompt": ("prompt_version",),
        "retry": ("retry",),  # three retry fields differ: one dimension, one field
        "tool_schema": ("tool_schema_version",),
    }
    renamed = V1.model_copy(update={"version": "v9", "label": "x", "description": "y"})
    assert ablatable_dimensions(V1, renamed) == {}
    # Another agent is a difference no field can change on its own (dimension "other").
    other = V1.model_copy(update={"agent": "task_planner", "prompt_version": "p2"})
    assert ablatable_dimensions(V1, other) == {"prompt": ("prompt_version",)}


def test_variant_configs_take_exactly_their_fields_from_the_other_side() -> None:
    fields = ablatable_dimensions(V1, V2)
    variants = by_key(plan_variants(V1, V2, fields))
    assert list(variants)[:4] == [
        "forward:graph",
        "backward:graph",
        "forward:isolation",
        "backward:isolation",
    ]
    assert len(variants) == 2 * 5 + 10  # forward and backward per dimension, then pairs

    forward = variants["forward:isolation"]
    assert forward.direction is AblationDirection.FORWARD
    assert forward.paths == ("isolate_untrusted",)
    assert forward.config.model_dump(exclude={"version", "label", "description"}) == {
        **V1.model_dump(exclude={"version", "label", "description"}),
        "isolate_untrusted": True,
    }
    backward = variants["backward:retry"]
    assert backward.config.retry == V1.retry
    assert backward.config.model_dump(exclude={"retry", "version", "label", "description"}) == (
        V2.model_dump(exclude={"retry", "version", "label", "description"})
    )
    pair = variants["pair:isolation+prompt"]
    assert (pair.config.isolate_untrusted, pair.config.prompt_version) == (True, "p2")
    assert pair.config.graph == V1.graph

    assert forward.config.version == "v1-with-isolation"
    assert backward.config.version == "v2-without-retry"
    assert pair.config.version == "v1-with-isolation-prompt"
    assert (forward.config.label, backward.config.label, pair.config.label) == (
        "A + isolation",
        "B - retry",
        "A + isolation + prompt",
    )
    versions = [v.config.version for v in variants.values()]
    assert len(set(versions)) == len(versions)
    assert all(SLUG.match(version) for version in versions)
    assert not [v for v in plan_variants(V1, V2, fields, pairs=False) if v.is_pair]


def test_variant_names_stay_valid_and_unique_when_long_or_taken() -> None:
    long_a = A.model_copy(update={"version": "a" * 64})
    variants = by_key(plan_variants(long_a, B, ablatable_dimensions(long_a, B)))
    for variant in variants.values():
        assert SLUG.match(variant.config.version), variant.config.version
    assert variants["forward:isolation"].config.version == "a" * 49 + "-with-isolation"
    assert variants["pair:isolation+prompt"].config.version.endswith("-with-isolation-prompt")

    # B's version is what A's forward prompt variant would be called: it must not reuse it.
    taken = B.model_copy(update={"version": "v1-with-prompt"})
    names = {
        v.key: v.config.version for v in plan_variants(A, taken, ablatable_dimensions(A, taken))
    }
    assert names["forward:prompt"] == "v1-with-prompt-2"
    assert len(set(names.values()) | {"v1", "v1-with-prompt"}) == len(names) + 2


# --- attribution ----------------------------------------------------------------------

#                c1 c2 c3 c4 c5 c6 c7 c8
ROWS = {
    "a": "TFFFTFTF",
    "b": "TTTFFTFT",
    "forward:isolation": "TTFFTFFF",
    "forward:prompt": "TFFFF-FF",
    "backward:isolation": "TFTFFTFF",
    "backward:prompt": "TTFFTTFF",
    "pair:isolation+prompt": "TTTFFTFF",
}


def scenario() -> tuple[SuiteResult, SuiteResult, list[tuple[Variant, SuiteResult]]]:
    """Six compared cases. c7's content changed in B and A could not judge c8, so neither is
    compared. Fixes: c2, c3, c6; regression: c5."""
    a_row = verdicts(ROWS["a"])
    a_row["c8"] = None
    a = suite(A, a_row)
    b = suite(B, verdicts(ROWS["b"]), hashes={"c7": "sha256:changed"})
    planned = plan_variants(A, B, ablatable_dimensions(A, B))
    evaluated = [(v, suite(v.config, verdicts(ROWS[v.key]))) for v in planned]
    return a, b, evaluated


def test_effects_are_exact_percentage_points_over_the_compared_cases() -> None:
    a, b, evaluated = scenario()
    ablation = attribute(a, b, evaluated, name="unit", ablation_id="abl-1", created_at=T0)
    assert ablation.n == 6
    assert ablation.dimensions == ["isolation", "prompt"]
    assert ablation.deterministic
    sixth = Fraction(100, 6)
    effects = {(v.direction.value, v.dimension): v for v in ablation.variants}
    forward_isolation = effects["forward", "isolation"]
    assert (forward_isolation.passed, forward_isolation.total) == (3, 6)
    assert forward_isolation.effect_pp == float(sixth)
    assert (forward_isolation.gained, forward_isolation.lost) == (["c2"], [])
    forward_prompt = effects["forward", "prompt"]
    assert forward_prompt.effect_pp == float(-sixth)
    assert (forward_prompt.gained, forward_prompt.lost) == ([], ["c5"])
    assert forward_prompt.harness_errors == 1
    backward_isolation = effects["backward", "isolation"]
    assert backward_isolation.effect_pp == float(sixth)  # B passes 4, B - isolation 3
    assert (backward_isolation.gained, backward_isolation.lost) == (["c2"], [])
    backward_prompt = effects["backward", "prompt"]
    assert backward_prompt.effect_pp == 0.0
    assert (backward_prompt.gained, backward_prompt.lost) == (["c3"], ["c5"])

    # Every effect is the difference of exact counts: gained minus lost, over n.
    for variant in ablation.variants:
        assert variant.effect_pp == float(
            Fraction(100 * (len(variant.gained) - len(variant.lost)), 6)
        )

    assert ablation.total_effect_pp == float(2 * sixth)
    assert ablation.sum_of_solo_effects_pp == 0.0  # +1/6 and -1/6 cancel exactly
    assert ablation.interaction_pp == float(2 * sixth)
    (pair,) = ablation.pairs
    assert pair.dimensions == ["isolation", "prompt"]
    assert (pair.passed, pair.effect_pp) == (4, float(2 * sixth))
    assert pair.interaction_pp == float(2 * sixth - sixth + sixth)
    assert (pair.gained, pair.lost) == (["c2", "c3", "c6"], ["c5"])
    assert not ablation.pairs_skipped


def test_rounding_happens_once_at_the_boundary() -> None:
    """Over 7 cases, 3/7 + 1/7 - 4/7 in floats is not 0; the exact interaction is."""
    rows = {
        "a": "FFFFFFF",
        "b": "TTTTFFF",
        "forward:isolation": "TTTFFFF",
        "forward:prompt": "FFFTFFF",
        "backward:isolation": "FFFFFFF",
        "backward:prompt": "FFFFFFF",
        "pair:isolation+prompt": "TTTTFFF",
    }
    a, b = suite(A, verdicts(rows["a"])), suite(B, verdicts(rows["b"]))
    planned = plan_variants(A, B, ablatable_dimensions(A, B))
    ablation = attribute(
        a, b, [(v, suite(v.config, verdicts(rows[v.key]))) for v in planned], name="x"
    )
    assert 300 / 7 + 100 / 7 - 400 / 7 != 0  # what naive float arithmetic would report
    assert ablation.interaction_pp == 0.0
    assert ablation.pairs[0].interaction_pp == 0.0
    assert ablation.total_effect_pp == float(Fraction(400, 7))


def test_flipped_cases_name_sufficient_and_necessary_dimensions() -> None:
    a, b, evaluated = scenario()
    cases = {c.case_id: c for c in attribute(a, b, evaluated, name="unit").cases}
    assert list(cases) == ["c2", "c3", "c5", "c6"]  # c7 changed content, c8 has no A verdict
    assert {c: cases[c].change for c in cases} == {
        "c2": CaseChange.FIX,
        "c3": CaseChange.FIX,
        "c5": CaseChange.REGRESSION,
        "c6": CaseChange.FIX,
    }
    # c2: isolation alone fixes it, and B fails it again without isolation.
    assert (cases["c2"].sufficient, cases["c2"].necessary) == (["isolation"], ["isolation"])
    assert not cases["c2"].needs_combination
    assert cases["c2"].sufficient_pairs == []
    # c3: no dimension alone fixes it, both together do; B needs the prompt for it.
    assert (cases["c3"].sufficient, cases["c3"].necessary) == ([], ["prompt"])
    assert cases["c3"].needs_combination
    assert cases["c3"].sufficient_pairs == [["isolation", "prompt"]]
    # c5 regressed: the prompt alone breaks it, and B without the prompt passes it again.
    assert (cases["c5"].sufficient, cases["c5"].necessary) == (["prompt"], ["prompt"])
    # c6: A + prompt could not judge it, which is no evidence either way.
    assert (cases["c6"].sufficient, cases["c6"].necessary) == ([], [])
    assert cases["c6"].sufficient_pairs == [["isolation", "prompt"]]
    assert cases["c6"].runs == {
        "a": "run-v1-c6",
        "b": "run-v1b-c6",
        "forward:isolation": "run-v1-with-isolation-c6",
        "backward:isolation": "run-v1b-without-isolation-c6",
        "backward:prompt": "run-v1b-without-prompt-c6",
        "pair:isolation+prompt": "run-v1-with-isolation-prompt-c6",
    }


def test_caveats_for_uncompared_cases_and_unjudged_variants() -> None:
    a, b, evaluated = scenario()
    ablation = attribute(a, b, evaluated, name="unit", warnings=["from the caller"])
    assert ablation.not_ablated == []
    assert ablation.warnings == [
        # Carried over from the comparison of A and B (suite_diff).
        "resolved content (prompt text, tool schemas, output schema, graph, scripted model) "
        "was not recorded for suite-v1, suite-v1b, so changes beyond the listed configuration "
        "fields cannot be ruled out",
        "from the caller",
        "2 of 8 shared cases are not compared: their content changed, or AgentScope could "
        "not judge them on A or B",
        "research_assistant@v1-with-prompt: 1 of 6 compared cases could not be judged "
        "(harness or provider errors); they count as not passed and are not attributed",
    ]


def test_what_no_field_can_change_is_reported_not_ablated() -> None:
    """A newer AgentScope ran B: that is a difference, but no variant can isolate it, and
    the variants (run like B) differ from A in it too."""
    newer = environment(agentscope_version="1.1.0")
    a = suite(A, verdicts("TF"))
    b = suite(B, verdicts("TT"), env=newer)
    planned = plan_variants(A, B, ablatable_dimensions(A, B), pairs=False)
    evaluated = [(v, suite(v.config, verdicts("TF"), env=newer)) for v in planned]
    ablation = attribute(a, b, evaluated, name="env", pairs_skipped=True)
    assert [(d.path, d.dimension) for d in ablation.not_ablated] == [
        ("environment.agentscope_version", "other")
    ]
    assert ablation.pairs == []
    assert ablation.pairs_skipped
    assert any(
        w.startswith(
            "variants ran in another environment than A or B "
            "(environment.agentscope_version differ)"
        )
        for w in ablation.warnings
    )


def test_a_simulated_and_a_real_model_are_not_deterministic() -> None:
    real = environment(provider="ollama", model="llama", deterministic=False)
    a = suite(A, verdicts("TF"))
    b = suite(B, verdicts("TT"), env=real)
    planned = plan_variants(A, B, ablatable_dimensions(A, B), pairs=False)
    ablation = attribute(a, b, [(v, suite(v.config, verdicts("TF"))) for v in planned], name="x")
    assert not ablation.deterministic
    assert any("effects may be noise" in warning for warning in ablation.warnings)


def test_a_pair_needs_both_of_its_forward_variants() -> None:
    a, b, evaluated = scenario()
    without_forward = [(v, s) for v, s in evaluated if v.key != "forward:prompt"]
    with pytest.raises(ValueError, match="needs both forward variants"):
        attribute(a, b, without_forward, name="x")
    # Skipped variants (validated away before running) leave their dimension unablated.
    solo = [(v, s) for v, s in evaluated if v.key in ("forward:isolation", "backward:isolation")]
    ablation = attribute(a, b, solo, name="x")
    assert ablation.dimensions == ["isolation"]
    assert [d.path for d in ablation.not_ablated] == ["prompt_version"]
    assert ablation.sum_of_solo_effects_pp == float(Fraction(100, 6))


def test_no_compared_case_means_no_effect() -> None:
    a = suite(A, {"c1": None})
    b = suite(B, {"c1": True})
    planned = plan_variants(A, B, ablatable_dimensions(A, B), pairs=False)
    ablation = attribute(a, b, [(v, suite(v.config, {"c1": True})) for v in planned], name="x")
    assert (ablation.n, ablation.total_effect_pp, ablation.cases) == (0, 0.0, [])
    assert all(v.effect_pp == 0.0 and v.total == 0 for v in ablation.variants)
