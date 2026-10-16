from __future__ import annotations

from agentscope.demo import DEMO_RUNS, DEMO_STEPS, seed_demo
from agentscope.services import Services


def counts(services: Services) -> tuple[int, int, int]:
    repository = services.repository
    return (
        repository.list_runs(limit=1)[1],
        repository.list_suites(limit=1)[1],
        repository.list_experiments(limit=1)[1],
    )


async def test_seed_tells_the_story(services: Services) -> None:
    progress: list[tuple[int, int]] = []
    summary = await seed_demo(services, progress=lambda done, total: progress.append((done, total)))
    assert progress[-1] == (DEMO_STEPS, DEMO_STEPS)
    assert summary.created == {"suites": 5, "experiments": 2, "runs": len(DEMO_RUNS)}

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

    clean = services.repository.get_run(summary.runs["demo-clean-answer"])
    assert clean is not None
    assert clean[2] is not None
    assert clean[2].passed


async def test_seeding_twice_adds_nothing(services: Services) -> None:
    first = await seed_demo(services)
    before = counts(services)
    second = await seed_demo(services)
    assert counts(services) == before
    assert second.created == {"suites": 0, "experiments": 0, "runs": 0}
    assert second.reused == first.created
    assert (second.suites, second.experiments, second.runs) == (
        first.suites,
        first.experiments,
        first.runs,
    )


async def test_an_errored_demo_suite_is_run_again(services: Services) -> None:
    first = await seed_demo(services)
    errored = first.suites["research_assistant@v2"]
    services.repository.fail_suite(errored, "interrupted")
    second = await seed_demo(services)
    assert second.created["suites"] == 1
    assert second.suites["research_assistant@v2"] != errored
