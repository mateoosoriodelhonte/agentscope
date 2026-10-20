"""Ablations through the services layer: research v1 -> v2 on the full built-in dataset
(the numbers below were measured with the deterministic fake model and are pinned), reuse of
a stored comparison's suites, and every refusal raised before anything runs."""

from __future__ import annotations

import asyncio
import os
from collections.abc import Iterator
from fractions import Fraction
from pathlib import Path

import pytest

from agentscope import services as services_module
from agentscope.config import Settings
from agentscope.datasets.packaged import UnknownDatasetError
from agentscope.domain.ablations import Ablation, AblationDirection
from agentscope.domain.agents import AgentConfig
from agentscope.domain.datasets import Dataset
from agentscope.services import (
    AblationRefused,
    Services,
    UnknownExperimentError,
    build_services,
)

V1, V2 = "research_assistant@v1", "research_assistant@v2"


def pp(part: int, whole: int = 29) -> float:
    return float(Fraction(100 * part, whole))


@pytest.fixture(scope="module")
def research(
    tmp_path_factory: pytest.TempPathFactory,
) -> Iterator[tuple[Services, Ablation, list[tuple[int, int]]]]:
    """One full ablation (A, B, 10 single-dimension variants, 10 pairs: 22 suites), shared
    by the tests that only read it."""
    with pytest.MonkeyPatch.context() as patch:
        for key in [k for k in os.environ if k.startswith("AGENTSCOPE_")]:
            patch.delenv(key)
        directory: Path = tmp_path_factory.mktemp("ablation")
        services = build_services(Settings(data_dir=directory, enable_ollama=False))
        progress: list[tuple[int, int]] = []
        ablation = asyncio.run(
            services.run_ablation(
                "research v1 vs v2",
                services.stored_dataset("research-assistant", "1"),
                services.catalog.resolve(V1),
                services.catalog.resolve(V2),
                progress=lambda done, total: progress.append((done, total)),
            )
        )
    yield services, ablation, progress
    services.repository.engine.dispose()


def test_research_v1_to_v2_is_attributed(
    research: tuple[Services, Ablation, list[tuple[int, int]]],
) -> None:
    _, ablation, _ = research
    assert (ablation.a.agent_ref, ablation.b.agent_ref) == (V1, V2)
    assert (ablation.a.metrics.passed, ablation.b.metrics.passed, ablation.n) == (5, 21, 29)
    assert ablation.deterministic
    assert ablation.dimensions == ["graph", "isolation", "prompt", "retry", "tool_schema"]
    assert ablation.not_ablated == []
    assert ablation.warnings == []

    rows = {
        (v.dimension, v.direction): (v.passed, v.effect_pp, len(v.gained), len(v.lost))
        for v in ablation.variants
    }
    forward, backward = AblationDirection.FORWARD, AblationDirection.BACKWARD
    assert rows == {
        ("graph", forward): (5, 0.0, 0, 0),
        ("graph", backward): (20, pp(1), 1, 0),
        ("isolation", forward): (5, 0.0, 0, 0),
        ("isolation", backward): (12, pp(9), 9, 0),
        ("prompt", forward): (5, 0.0, 0, 0),
        ("prompt", backward): (12, pp(9), 9, 0),
        ("retry", forward): (8, pp(3), 3, 0),
        ("retry", backward): (17, pp(4), 4, 0),
        ("tool_schema", forward): (8, pp(3), 3, 0),
        ("tool_schema", backward): (21, 0.0, 0, 0),
    }
    assert ablation.total_effect_pp == pp(16)  # +55.2 pp
    assert ablation.sum_of_solo_effects_pp == pp(6)  # +20.7 pp
    assert ablation.interaction_pp == pp(10)  # +34.5 pp

    # The instruction-hierarchy prompt and isolation work only together: one pair, +41.4 pp.
    interactions = {"+".join(p.dimensions): p.interaction_pp for p in ablation.pairs}
    assert len(interactions) == 10
    assert interactions["isolation+prompt"] == pp(12)
    assert {k: v for k, v in interactions.items() if v != 0} == {"isolation+prompt": pp(12)}
    pair = next(p for p in ablation.pairs if p.dimensions == ["isolation", "prompt"])
    assert (pair.passed, pair.effect_pp, pair.agent_ref) == (
        17,
        pp(12),
        "research_assistant@v1-with-isolation-prompt",
    )


def test_research_flips_are_attributed_case_by_case(
    research: tuple[Services, Ablation, list[tuple[int, int]]],
) -> None:
    _, ablation, _ = research
    assert len(ablation.cases) == 16
    assert all(c.change.value == "fix" for c in ablation.cases)
    sufficient = {d: sum(d in c.sufficient for c in ablation.cases) for d in ablation.dimensions}
    necessary = {d: sum(d in c.necessary for c in ablation.cases) for d in ablation.dimensions}
    assert sufficient == {"graph": 0, "isolation": 0, "prompt": 0, "retry": 3, "tool_schema": 3}
    assert necessary == {"graph": 1, "isolation": 9, "prompt": 9, "retry": 4, "tool_schema": 0}
    together = [c for c in ablation.cases if c.sufficient_pairs]
    assert len(together) == 9
    for case in together:
        assert case.needs_combination
        assert case.sufficient_pairs == [["isolation", "prompt"]]
        assert case.necessary == ["isolation", "prompt"]
        assert case.case_id.startswith("injection-")
        assert set(case.runs) == {"a", "b"} | {
            f"{direction}:{d}" for d in ablation.dimensions for direction in ("forward", "backward")
        } | {"pair:" + "+".join(p.dimensions) for p in ablation.pairs}


def test_variants_are_stored_linked_and_hidden(
    research: tuple[Services, Ablation, list[tuple[int, int]]],
) -> None:
    services, ablation, progress = research
    assert progress[-1] == (22 * 29, 22 * 29)
    assert [done for done, _ in progress] == sorted(done for done, _ in progress)
    assert services.repository.get_ablation(ablation.ablation_id) == ablation

    repository = services.repository
    assert {s.suite_id for s in repository.list_suites()[0]} == {
        ablation.a.suite_id,
        ablation.b.suite_id,
    }
    assert repository.list_suites(include_ablations=True)[1] == 22
    variant = ablation.variants[0]
    suite = repository.get_suite(variant.suite_id)
    assert suite is not None
    assert suite.agent_config.ref == variant.agent_ref == "research_assistant@v1-with-graph"
    assert suite.label == "research v1 vs v2 (A + graph)"
    # Their traces open like any other.
    case = next(c for c in ablation.cases if "forward:graph" in c.runs)
    assert repository.get_run(case.runs["forward:graph"]) is not None


async def test_a_stored_comparison_is_explained_with_its_own_suites(
    services: Services, small_research: Dataset
) -> None:
    services.import_dataset(small_research)
    comparison = await services.run_experiment(
        "small v1 vs v2", small_research, services.catalog.resolve(V1), services.catalog.resolve(V2)
    )
    ablation = await services.ablate_experiment(comparison.experiment_id, pairs=False)
    assert ablation.name == "small v1 vs v2"
    assert ablation.experiment_id == comparison.experiment_id
    assert (ablation.a.suite_id, ablation.b.suite_id) == (
        comparison.a.suite_id,
        comparison.b.suite_id,
    )
    assert (ablation.pairs, ablation.pairs_skipped) == ([], True)
    assert len(ablation.variants) == 10
    assert ablation.n == 3
    # Only the variant suites ran: two suites were reused, ten were added.
    assert services.repository.list_suites(include_ablations=True)[1] == 12
    stored = services.repository.latest_ablation_for_experiment(comparison.experiment_id)
    assert stored == ablation


def test_refusals_come_before_anything_runs(
    services: Services, small_research: Dataset, monkeypatch: pytest.MonkeyPatch
) -> None:
    v1, v2 = services.catalog.resolve(V1), services.catalog.resolve(V2)
    renamed = v1.model_copy(update={"version": "v1-again", "label": "same behaviour"})
    with pytest.raises(AblationRefused, match="nothing to ablate"):
        services.plan_ablation("x", small_research, v1, renamed)
    with pytest.raises(UnknownExperimentError):
        services.plan_experiment_ablation("00000000-0000-0000-0000-000000000000")

    monkeypatch.setattr(services_module, "MAX_ABLATION_DIMENSIONS", 4)
    with pytest.raises(AblationRefused, match="differ in 5 dimensions"):
        services.plan_ablation("x", small_research, v1, v2)
    monkeypatch.undo()
    assert services.repository.list_suites(include_ablations=True)[1] == 0

    monkeypatch.setattr(services_module, "MAX_PAIRWISE_DIMENSIONS", 4)
    plan = services.plan_ablation("x", small_research, v1, v2)
    assert plan.pairs_skipped
    assert [v.key for v in plan.variants if v.is_pair] == []
    assert (plan.suites, plan.total) == (12, 36)


async def test_every_suite_must_have_evaluated_the_same_dataset_version(
    services: Services, small_research: Dataset
) -> None:
    v1, v2 = services.catalog.resolve(V1), services.catalog.resolve(V2)
    other = small_research.model_copy(update={"version": "2", "cases": small_research.cases[:2]})
    services.import_dataset(small_research)
    services.import_dataset(other)
    a = await services.run_suite(small_research, v1)
    b = await services.run_suite(other, v2)
    with pytest.raises(AblationRefused, match="same dataset version"):
        services.plan_ablation("x", small_research, v1, v2, a_suite=a, b_suite=b)
    comparison, _ = services.compare_suites("mismatched", a.suite_id, b.suite_id)
    assert comparison.dataset_mismatch
    with pytest.raises(AblationRefused, match="same dataset version"):
        services.plan_experiment_ablation(comparison.experiment_id)

    # A suite of a dataset that is no longer stored (or never was) cannot be reused either.
    unstored = Dataset(name="unstored", agent="research_assistant", cases=other.cases)
    c = await services.run_suite(unstored, v1, save=True)
    d = await services.run_suite(unstored, v2, save=True)
    stray, _ = services.compare_suites("stray", c.suite_id, d.suite_id)
    with pytest.raises(UnknownDatasetError):
        services.plan_experiment_ablation(stray.experiment_id)


def test_an_invalid_variant_is_skipped_with_a_warning(
    services: Services, small_research: Dataset, monkeypatch: pytest.MonkeyPatch
) -> None:
    check = Services.check_suite_inputs
    refused = {"v1-with-prompt"}

    def refuse(self: Services, dataset: Dataset, config: AgentConfig) -> object:
        if config.version in refused:
            raise ValueError(f"not {config.version}")
        return check(self, dataset, config)

    monkeypatch.setattr(Services, "check_suite_inputs", refuse)
    v1, v2 = services.catalog.resolve(V1), services.catalog.resolve(V2)
    plan = services.plan_ablation("x", small_research, v1, v2)
    keys = [v.key for v in plan.variants]
    assert "forward:prompt" not in keys
    assert "backward:prompt" in keys
    # Every pair with the prompt needs A + prompt to measure its interaction.
    assert [k for k in keys if k.startswith("pair:") and "prompt" in k] == []
    assert len([k for k in keys if k.startswith("pair:")]) == 6
    assert plan.warnings[0] == (
        "research_assistant@v1-with-prompt (A + prompt) skipped: not v1-with-prompt"
    )
    assert len(plan.warnings) == 1 + 4
    assert all("single-dimension variants were skipped" in w for w in plan.warnings[1:])

    refused.update(v.config.version for v in plan.variants)  # now every variant
    with pytest.raises(AblationRefused, match=r"^nothing to ablate: .*v2-without-graph"):
        services.plan_ablation("x", small_research, v1, v2)
