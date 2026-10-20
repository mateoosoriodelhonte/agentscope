from __future__ import annotations

from fractions import Fraction

import pytest
from sqlalchemy import delete, update

from agentscope import demo
from agentscope.demo import DEMO_RUNS, demo_steps, seed_demo
from agentscope.services import Services
from agentscope.storage.models import DatasetRow, ExperimentRow


def counts(services: Services) -> tuple[int, ...]:
    repository = services.repository
    return (
        repository.list_runs(limit=1)[1],
        repository.list_suites(limit=1)[1],
        repository.list_suites(include_ablations=True, limit=1)[1],
        repository.list_experiments(limit=1)[1],
        repository.list_ablations(limit=1)[1],
    )


@pytest.fixture
def without_ablation(monkeypatch: pytest.MonkeyPatch) -> None:
    """For tests about other parts of the story: the ablation alone evaluates 20 suites."""
    monkeypatch.setattr(demo, "DEMO_ABLATIONS", ())


async def test_seed_tells_the_story(services: Services) -> None:
    progress: list[tuple[int, int]] = []
    summary = await seed_demo(services, progress=lambda done, total: progress.append((done, total)))
    assert progress[-1] == (demo_steps(), demo_steps())
    assert summary.created == {
        "suites": 5,
        "experiments": 2,
        "ablations": 1,
        "runs": len(DEMO_RUNS),
    }

    gates = {}
    for agent_ref, suite_id in summary.suites.items():
        suite = services.repository.get_suite(suite_id)
        assert suite is not None
        assert suite.gate is not None
        gates[agent_ref] = suite.gate.passed
    assert gates == {
        "research_assistant@v1": False,
        "research_assistant@v2": True,
        "research_assistant@v3": True,
        "task_planner@v1": False,
        "task_planner@v2": True,
    }

    for name, experiment_id in summary.experiments.items():
        comparison = services.repository.get_experiment(experiment_id)
        assert comparison is not None
        assert comparison.name == name
    v1_v2 = services.repository.get_experiment(summary.experiments["Demo: research v1 vs v2"])
    v2_v3 = services.repository.get_experiment(summary.experiments["Demo: research v2 vs v3"])
    assert v1_v2 is not None
    assert v1_v2.confounded
    assert v2_v3 is not None
    assert not v2_v3.confounded
    assert {d.dimension for d in v2_v3.config_diff} == {"model"}

    # A fully compromised model under v2: it tries the injected action, the guard blocks it.
    found = services.repository.get_run(summary.runs["demo-injection-compliant-model"])
    assert found is not None
    trace, _case, evaluation = found
    assert trace.config.model.model == "scripted-compliant"
    assert evaluation is not None
    assert evaluation.unauthorized_tool_attempts > 0
    assert evaluation.unauthorized_tool_executions == 0
    assert all(not inv.executed for inv in trace.tool_invocations if inv.tool == "send_email")

    # The ablation explains v1 vs v2 with that comparison's own suites; its 20 variant
    # suites are stored but listed only on request.
    ablation = services.repository.get_ablation(summary.ablations["Demo: research v1 vs v2"])
    assert ablation is not None
    assert ablation.experiment_id == v1_v2.experiment_id
    assert (ablation.a.suite_id, ablation.b.suite_id) == (v1_v2.a.suite_id, v1_v2.b.suite_id)
    assert ablation.deterministic
    interactions = {"+".join(p.dimensions): p.interaction_pp for p in ablation.pairs}
    assert {k: v for k, v in interactions.items() if v} == {
        "isolation+prompt": float(Fraction(1200, 29))
    }
    assert services.repository.list_suites()[1] == 5
    assert services.repository.list_suites(include_ablations=True)[1] == 5 + 20

    clean = services.repository.get_run(summary.runs["demo-clean-answer"])
    assert clean is not None
    assert clean[2] is not None
    assert clean[2].passed


async def test_seeding_twice_adds_nothing(services: Services) -> None:
    first = await seed_demo(services)
    before = counts(services)
    second = await seed_demo(services)
    assert counts(services) == before
    assert second.created == {"suites": 0, "experiments": 0, "ablations": 0, "runs": 0}
    assert second.reused == first.created
    assert (second.suites, second.experiments, second.ablations, second.runs) == (
        first.suites,
        first.experiments,
        first.ablations,
        first.runs,
    )


@pytest.mark.usefixtures("without_ablation")
async def test_an_errored_demo_suite_is_run_again(services: Services) -> None:
    first = await seed_demo(services)
    errored = first.suites["research_assistant@v2"]
    services.repository.fail_suite(errored, "interrupted")
    second = await seed_demo(services)
    assert second.created["suites"] == 1
    assert second.suites["research_assistant@v2"] != errored


@pytest.mark.usefixtures("without_ablation")
async def test_seeded_suites_evaluate_stored_datasets(services: Services) -> None:
    """Every seeded suite links to a dataset the API can show, even when the packaged
    datasets went missing from storage (an earlier purge of every dataset)."""
    with services.repository.session() as session, session.begin():
        session.execute(delete(DatasetRow))
    summary = await seed_demo(services)
    for suite_id in summary.suites.values():
        suite = services.repository.get_suite(suite_id)
        assert suite is not None
        stored = services.repository.get_dataset(suite.dataset_name, suite.dataset_version)
        assert stored is not None
        assert stored.summary.content_hash == suite.dataset_hash


@pytest.mark.usefixtures("without_ablation")
async def test_experiments_already_compared_under_another_name_are_reused(
    services: Services,
) -> None:
    first = await seed_demo(services)
    v1, v2 = first.suites["research_assistant@v1"], first.suites["research_assistant@v2"]
    with services.repository.session() as session, session.begin():
        session.execute(
            delete(ExperimentRow).where(ExperimentRow.name != "Demo: research v1 vs v2")
        )
        session.execute(update(ExperimentRow).values(name="mine"))
    second = await seed_demo(services)
    assert second.created["experiments"] == 1  # only v2 vs v3 was missing
    stored = services.repository.find_experiment(v1, v2)
    assert stored is not None
    assert second.experiments["Demo: research v1 vs v2"] == stored.experiment_id
