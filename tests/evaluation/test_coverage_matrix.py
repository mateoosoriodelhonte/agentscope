"""Meta-test: every check can both pass and fail, and the always-on checks always apply.

A check that never fails in any scenario would pass vacuously in real suites; one that
never passes would be noise. Every scenario is evaluated with all ten checks, not only
the one it targets.
"""

from __future__ import annotations

from factories import evaluate
from scenarios import SCENARIOS

from agentscope.domain.evaluation import CheckName, CheckStatus

ALWAYS_APPLY = {
    CheckName.SCHEMA_VALIDITY,
    CheckName.TASK_COMPLETION,
    CheckName.POLICY_COMPLIANCE,
    CheckName.FORBIDDEN_TOOL_CALLS,
}


def _matrix() -> dict[CheckName, set[CheckStatus]]:
    seen: dict[CheckName, set[CheckStatus]] = {name: set() for name in CheckName}
    for s in SCENARIOS:
        for check in evaluate(s.case, s.trace).checks:
            seen[check.name].add(check.status)
    return seen


def test_check_coverage_matrix() -> None:
    matrix = _matrix()
    missing = {
        name.value: sorted(
            status.value
            for status in (CheckStatus.PASSED, CheckStatus.FAILED)
            if status not in seen
        )
        for name, seen in matrix.items()
        if not {CheckStatus.PASSED, CheckStatus.FAILED} <= seen
    }
    assert missing == {}


def test_applicability_table() -> None:
    matrix = _matrix()
    for name, seen in matrix.items():
        assert (CheckStatus.NOT_APPLICABLE in seen) is (name not in ALWAYS_APPLY), name


def test_every_check_is_targeted_by_a_passing_and_a_failing_scenario() -> None:
    targeted = {(s.check, s.status) for s in SCENARIOS}
    for name in CheckName:
        assert (name, CheckStatus.PASSED) in targeted, name
        assert (name, CheckStatus.FAILED) in targeted, name
