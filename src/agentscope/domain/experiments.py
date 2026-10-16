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
    STILL_FAILING = "still_failing"


ConfigDimension = Literal[
    "prompt", "model", "tool_schema", "graph", "retry", "isolation", "tools", "limits", "other"
]


class ConfigDifference(StrictModel):
    path: str
    dimension: ConfigDimension
    a: JsonValue = None
    b: JsonValue = None


class MetricDelta(StrictModel):
    metric: str
    a: float
    b: float
    delta: float
    higher_is_better: bool

    @property
    def improved(self) -> bool | None:
        if self.delta == 0:
            return None
        return (self.delta > 0) == self.higher_is_better


class CaseComparison(StrictModel):
    case_id: str
    a_passed: bool
    b_passed: bool
    change: CaseChange
    a_failed_checks: list[str] = Field(default_factory=list)
    b_failed_checks: list[str] = Field(default_factory=list)


class SuiteSummary(StrictModel):
    suite_id: str
    agent_ref: str
    config_hash: str
    metrics: SuiteMetrics


class Comparison(StrictModel):
    experiment_id: str
    name: str
    created_at: datetime
    dataset_name: str
    dataset_version: str
    dataset_hash: str
    a: SuiteSummary
    b: SuiteSummary
    metric_deltas: list[MetricDelta]
    cases: list[CaseComparison]
    config_diff: list[ConfigDifference]
