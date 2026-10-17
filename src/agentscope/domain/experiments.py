"""Experiment contracts: comparing two agent versions on one dataset version."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import Field, JsonValue

from agentscope.domain.common import StrictModel
from agentscope.domain.evaluation import SuiteMetrics


class CaseChange(StrEnum):
    REGRESSION = "regression"  # passed in A, fails in B
    FIX = "fix"  # failed in A, passes in B
    STILL_PASSING = "still_passing"
    STILL_FAILING_CHANGED = "still_failing_changed"  # fails in both, different failed checks
    STILL_FAILING_SAME = "still_failing_same"
    ADDED = "added"  # only in B
    REMOVED = "removed"  # only in A
    CONTENT_CHANGED = "content_changed"  # same id, different case content: not comparable
    # AgentScope failed on at least one side, so there is no agent verdict to compare.
    HARNESS_ERROR = "harness_error"


ConfigDimension = Literal[
    "prompt", "model", "tool_schema", "graph", "retry", "isolation", "tools", "limits", "other"
]


class ConfigDifference(StrictModel):
    path: str
    dimension: ConfigDimension
    a: JsonValue = None
    b: JsonValue = None


class MetricDelta(StrictModel):
    """Absolute counts or percentage points — never relative percentages."""

    metric: str
    unit: Literal["count", "pp", "steps"]
    a: float
    b: float
    delta: float
    higher_is_better: bool
    n: int = Field(ge=0)

    @property
    def improved(self) -> bool | None:
        if self.delta == 0:
            return None
        return (self.delta > 0) == self.higher_is_better


class CheckFlip(StrictModel):
    check: str
    regressions: int = Field(ge=0)
    fixes: int = Field(ge=0)


class CaseComparison(StrictModel):
    case_id: str
    change: CaseChange
    a_passed: bool | None
    b_passed: bool | None
    a_failed_checks: list[str] = Field(default_factory=list)
    b_failed_checks: list[str] = Field(default_factory=list)
    a_run_id: str | None = None
    b_run_id: str | None = None


class SuiteSummary(StrictModel):
    suite_id: str
    agent_ref: str
    config_hash: str
    effective_config_hash: str
    dataset_hash: str
    metrics: SuiteMetrics


class Comparison(StrictModel):
    experiment_id: str
    name: str
    created_at: datetime
    dataset_name: str
    dataset_version: str
    dataset_hash: str
    dataset_mismatch: bool  # A and B ran different dataset content (only shared cases compared)
    deterministic: bool  # both sides used the deterministic fake provider
    a: SuiteSummary
    b: SuiteSummary
    metric_deltas: list[MetricDelta]
    check_flips: list[CheckFlip]
    cases: list[CaseComparison]
    config_diff: list[ConfigDifference]
    # More than one configuration dimension changed, so no single change can be credited.
    confounded: bool
    # Caveats that are not configuration differences: verdicts judged by different check
    # engines, or resolved content that was not recorded and so cannot be compared.
    warnings: list[str] = Field(default_factory=list)
