"""Failure analysis and the overview, on a database seeded with the demo story."""

from __future__ import annotations

import asyncio
import os
from collections.abc import Iterator
from pathlib import Path

import pytest

from agentscope.analysis import failure_analysis, overview
from agentscope.api.schemas import ProviderStatus
from agentscope.config import Settings
from agentscope.demo import DemoSummary, seed_demo
from agentscope.services import Services, build_services

V1, V2, V3 = (f"research_assistant@{v}" for v in ("v1", "v2", "v3"))


@pytest.fixture(scope="module")
def seeded(tmp_path_factory: pytest.TempPathFactory) -> Iterator[tuple[Services, DemoSummary]]:
    # Module scope runs before the per-test environment isolation, so isolate here too.
    with pytest.MonkeyPatch.context() as patch:
        for key in [k for k in os.environ if k.startswith("AGENTSCOPE_")]:
            patch.delenv(key)
        directory: Path = tmp_path_factory.mktemp("analysis")
        services = build_services(Settings(data_dir=directory, enable_ollama=False))
        summary = asyncio.run(seed_demo(services))
    yield services, summary
    services.repository.engine.dispose()


def test_default_selection_is_the_latest_suite_per_agent(
    seeded: tuple[Services, DemoSummary],
) -> None:
    services, summary = seeded
    analysis = failure_analysis(services.repository)
    assert set(analysis.suites_considered) == set(summary.suites.values())
    narrowed = failure_analysis(services.repository, agent_refs=[V2])
    assert narrowed.suites_considered == [summary.suites[V2]]
    explicit = failure_analysis(services.repository, suite_ids=[summary.suites[V3], "nope"])
    assert explicit.suites_considered == [summary.suites[V3]]
    assert failure_analysis(services.repository, suite_ids=["nope"]).resilience == []


def test_resilience_rows(seeded: tuple[Services, DemoSummary]) -> None:
    services, _ = seeded
    rows = failure_analysis(services.repository).resilience
    assert rows == sorted(rows, key=lambda r: (r.agent_ref, r.fault_kind))
    for row in rows:
        assert row.recovered + row.degraded + row.failed <= row.cases
        assert row.passed <= row.cases
    by_key = {(r.agent_ref, r.fault_kind): r for r in rows}
    # v3 (careful model) handles every injected tool failure; v1 does not.
    for kind in ("timeout", "error", "malformed", "incomplete"):
        assert by_key[V3, kind].failed == 0
        assert by_key[V3, kind].recovered + by_key[V3, kind].degraded >= 1
    assert sum(by_key[V1, kind].failed for kind in ("error", "malformed", "incomplete")) > 0
    assert by_key[V3, "timeout"].recovered >= 1


def test_failure_buckets(seeded: tuple[Services, DemoSummary]) -> None:
    services, _ = seeded
    buckets = failure_analysis(services.repository, agent_refs=[V1]).buckets
    assert buckets
    assert [b.count for b in buckets] == sorted((b.count for b in buckets), reverse=True)
    leak = [b for b in buckets if b.check == "policy_compliance"]
    assert leak
    assert leak[0].reason == "leaked_content"
    for bucket in buckets:
        assert 1 <= len(bucket.example_run_ids) <= min(3, bucket.count)
        for run_id in bucket.example_run_ids:
            assert services.repository.get_run(run_id) is not None
    assert any(b.fault_kind is None for b in buckets)  # corpus injections need no fault
    # v3 (careful model, hardened config) passes every research case, so nothing to bucket.
    assert failure_analysis(services.repository, agent_refs=[V3]).buckets == []


def test_injection_summary(seeded: tuple[Services, DemoSummary]) -> None:
    services, _ = seeded
    summary = {s.agent_ref: s for s in failure_analysis(services.repository).injection}
    assert summary[V1].unauthorized_attempts > 0
    assert summary[V1].leaked_cases > 0
    assert summary[V2].unauthorized_attempts == 0
    assert summary[V2].leaked_cases == 0
    for row in summary.values():
        assert row.unauthorized_executions == 0  # enforced by the runtime guard
        assert row.deterministic
        assert row.cases > 0


def test_injection_summary_counts_only_prompt_injection_cases(
    seeded: tuple[Services, DemoSummary],
) -> None:
    services, summary = seeded
    suite = services.repository.get_suite(summary.suites[V2])
    assert suite is not None
    injection_cases = [c for c in suite.cases if "prompt-injection" in c.tags]
    # The model-unknown-tool case attempts an unpermitted tool, but it is a model fault, not
    # an injection effect, so it must not show up in the injection summary.
    assert any(c.unauthorized_tool_attempts for c in suite.cases if c not in injection_cases)
    (row,) = [s for s in failure_analysis(services.repository).injection if s.agent_ref == V2]
    assert row.cases == len(injection_cases)
    assert row.unauthorized_attempts == 0


def test_overview(seeded: tuple[Services, DemoSummary]) -> None:
    services, summary = seeded
    providers = [ProviderStatus(name="fake", enabled=True, deterministic=True, reachable=True)]
    view = overview(services.repository, providers)
    runs = services.repository.list_runs(limit=1)[1]
    assert view.counts == {
        "runs": runs,
        "suites": 5,
        "datasets": len(services.repository.list_datasets()),
        "experiments": 2,
    }
    assert len(view.recent_runs) == 10
    assert len(view.recent_suites) == 5
    assert {s.agent_ref for s in view.pass_rate_series} == set(summary.suites)
    for series in view.pass_rate_series:
        assert [p.suite_id for p in series.points] == [summary.suites[series.agent_ref]]
        assert series.points[0].dataset.endswith("@1")
    assert view.latest_gate is not None
    assert view.providers == providers
