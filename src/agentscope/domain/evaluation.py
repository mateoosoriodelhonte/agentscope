"""Evaluation contracts: deterministic check results, suite metrics, and gates."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import Field, JsonValue

from agentscope.domain.agents import AgentConfig
from agentscope.domain.common import StrictModel
from agentscope.domain.traces import RunStatus


class CheckName(StrEnum):
    ANSWER_CORRECTNESS = "answer_correctness"
    SCHEMA_VALIDITY = "schema_validity"
    REQUIRED_TOOL_CALLS = "required_tool_calls"
    FORBIDDEN_TOOL_CALLS = "forbidden_tool_calls"
    TOOL_ORDER = "tool_order"
    TOOL_ARGUMENTS = "tool_arguments"
    TASK_COMPLETION = "task_completion"
    TIMEOUT_BEHAVIOR = "timeout_behavior"
    ERROR_RECOVERY = "error_recovery"
    POLICY_COMPLIANCE = "policy_compliance"


class CheckStatus(StrEnum):
    PASSED = "passed"
    FAILED = "failed"
    SKIPPED = "skipped"  # not applicable to this case


class CheckResult(StrictModel):
    name: CheckName
    status: CheckStatus
    message: str
    expected: JsonValue = None
    actual: JsonValue = None
    evidence_span_ids: list[str] = Field(default_factory=list)


class JudgeVerdict(StrictModel):
    """Optional model-based assessment. Stored separately; never affects pass/fail."""

    label: Literal["model-based judge (non-deterministic)"] = (
        "model-based judge (non-deterministic)"
    )
    provider: str
    model: str
    prompt_version: str
    temperature: float
    seed: int | None
    verdict: Literal["pass", "fail", "uncertain"]
    rationale: str = Field(max_length=4000)


class CaseResult(StrictModel):
    case_id: str
    run_id: str
    trace_id: str
    passed: bool
    status: RunStatus
    checks: list[CheckResult]
    steps: int = Field(ge=0)
    duration_ms: float = Field(ge=0)
    tool_errors: int = Field(ge=0)
    invalid_output: bool
    policy_violations: int = Field(ge=0)
    tags: list[str] = Field(default_factory=list)
    judge: JudgeVerdict | None = None


class CheckStats(StrictModel):
    passed: int = Field(default=0, ge=0)
    failed: int = Field(default=0, ge=0)
    skipped: int = Field(default=0, ge=0)

    @property
    def applicable(self) -> int:
        return self.passed + self.failed

    @property
    def pass_rate(self) -> float | None:
        return None if self.applicable == 0 else self.passed / self.applicable


class SuiteMetrics(StrictModel):
    total: int = Field(ge=0)
    passed: int = Field(ge=0)
    failed: int = Field(ge=0)
    pass_rate: float = Field(ge=0, le=1)
    tool_errors: int = Field(ge=0)
    invalid_outputs: int = Field(ge=0)
    policy_violations: int = Field(ge=0)
    run_timeouts: int = Field(ge=0)
    avg_steps: float = Field(ge=0)
    avg_duration_ms: float = Field(ge=0)
    checks: dict[CheckName, CheckStats] = Field(default_factory=dict)


class GateThresholds(StrictModel):
    min_pass_rate: float | None = Field(default=None, ge=0, le=1)
    max_tool_errors: int | None = Field(default=None, ge=0)
    max_invalid_outputs: int | None = Field(default=None, ge=0)
    max_policy_violations: int | None = Field(default=None, ge=0)
    # Checks that must pass on every case where they apply.
    required_checks: list[CheckName] = Field(default_factory=list)
    # Maximum allowed drop in pass rate versus a baseline result (absolute, 0-1).
    max_pass_rate_drop: float | None = Field(default=None, ge=0, le=1)


class GateResult(StrictModel):
    passed: bool
    failures: list[str] = Field(default_factory=list)
    thresholds: GateThresholds
    baseline_pass_rate: float | None = None


class SuiteResult(StrictModel):
    suite_id: str
    label: str = ""
    dataset_name: str
    dataset_version: str
    dataset_hash: str
    agent_config: AgentConfig
    config_hash: str
    agentscope_version: str
    started_at: datetime
    finished_at: datetime
    cases: list[CaseResult]
    metrics: SuiteMetrics
    gate: GateResult | None = None
