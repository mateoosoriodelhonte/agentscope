"""Evaluation contracts: deterministic check results, suite metrics, and gates.

Model-based judging is deliberately absent from these contracts so it can never leak into
pass/fail, metrics, or gates.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import Field, JsonValue

from agentscope.domain.agents import AgentConfig
from agentscope.domain.common import StrictModel
from agentscope.domain.traces import RunStatus

CHECK_ENGINE_VERSION = "1.0.0"


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
    NOT_APPLICABLE = "not_applicable"  # the case sets no expectation this check judges


class ReasonCode(StrEnum):
    OK = "ok"
    NO_EXPECTATION = "no_expectation"
    OUTPUT_MISSING = "output_missing"
    FIELD_MISSING = "field_missing"
    MISMATCH = "mismatch"
    TYPE_MISMATCH = "type_mismatch"
    SCHEMA_INVALID = "schema_invalid"
    CALL_MISSING = "call_missing"
    TOO_MANY_CALLS = "too_many_calls"
    ARGUMENTS_MISMATCH = "arguments_mismatch"
    ORDER_VIOLATED = "order_violated"
    FORBIDDEN_ATTEMPTED = "forbidden_attempted"
    UNAUTHORIZED_EXECUTION = "unauthorized_execution"
    LEAKED_CONTENT = "leaked_content"
    RUN_NOT_COMPLETED = "run_not_completed"
    STATUS_MISMATCH = "status_mismatch"
    BUDGET_EXCEEDED = "budget_exceeded"
    TIMEOUT_NOT_SURFACED = "timeout_not_surfaced"
    NOT_RECOVERED = "not_recovered"
    NOT_DEGRADED = "not_degraded"


EvidenceRole = Literal["violation", "support", "absent"]


class Evidence(StrictModel):
    """Points at the part of a trace that supports a verdict.

    `role="absent"` marks something that should exist but does not (so there is no span).
    """

    role: EvidenceRole
    detail: str = Field(max_length=500)
    span_id: str | None = None
    step: int | None = None
    tool: str | None = None
    call_id: str | None = None


class CheckResult(StrictModel):
    name: CheckName
    status: CheckStatus
    reason: ReasonCode
    message: str = Field(max_length=1000)
    expected: JsonValue = None
    actual: JsonValue = None
    evidence: list[Evidence] = Field(default_factory=list, max_length=50)


class CaseResult(StrictModel):
    case_id: str
    case_hash: str
    run_id: str | None
    trace_id: str | None
    passed: bool
    status: RunStatus | None
    # Set when the harness itself failed for this case (not an agent failure).
    harness_error: str | None = None
    checks: list[CheckResult] = Field(default_factory=list)
    steps: int = Field(default=0, ge=0)
    duration_ms: float = Field(default=0, ge=0)
    tags: list[str] = Field(default_factory=list)
    injected_fault_outcomes: int = Field(default=0, ge=0)
    unexpected_tool_errors: int = Field(default=0, ge=0)
    invalid_output: bool = False
    repaired_output: bool = False
    unauthorized_tool_attempts: int = Field(default=0, ge=0)
    unauthorized_tool_executions: int = Field(default=0, ge=0)


class CheckStats(StrictModel):
    passed: int = Field(default=0, ge=0)
    failed: int = Field(default=0, ge=0)
    not_applicable: int = Field(default=0, ge=0)

    @property
    def applicable(self) -> int:
        return self.passed + self.failed


class UsageSummary(StrictModel):
    """Provider-reported tokens across runs; partial when some runs reported none."""

    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    reporting_runs: int = Field(ge=0)
    total_runs: int = Field(ge=0)


class InformationalTimings(StrictModel):
    """Wall-clock timings. Non-deterministic: excluded from baselines and gates."""

    median_duration_ms: float = Field(ge=0)
    p95_duration_ms: float = Field(ge=0)


class SuiteMetrics(StrictModel):
    total: int = Field(ge=0)
    passed: int = Field(ge=0)
    failed: int = Field(ge=0)
    harness_errors: int = Field(ge=0)
    pass_rate: float = Field(ge=0, le=1)  # passed / total (harness errors count as not passed)
    run_statuses: dict[str, int] = Field(default_factory=dict)
    checks: dict[CheckName, CheckStats] = Field(default_factory=dict)
    injected_fault_outcomes: int = Field(ge=0)
    unexpected_tool_errors: int = Field(ge=0)
    invalid_outputs: int = Field(ge=0)
    repaired_outputs: int = Field(ge=0)
    policy_violation_cases: int = Field(ge=0)
    unauthorized_tool_attempts: int = Field(ge=0)
    unauthorized_tool_executions: int = Field(ge=0)
    avg_steps: float = Field(ge=0)
    usage: UsageSummary | None = None
    timings: InformationalTimings | None = None


class GateThresholds(StrictModel):
    """Regression-gate rules. Rates are compared exactly (rational arithmetic)."""

    min_pass_rate: float | None = Field(default=None, ge=0, le=1)
    max_new_regressions: int | None = Field(default=None, ge=0)  # vs baseline, per case
    max_pass_rate_drop_pp: int | None = Field(default=None, ge=0, le=100)  # vs baseline
    max_invalid_outputs: int | None = Field(default=None, ge=0)
    max_policy_violation_cases: int | None = Field(default=None, ge=0)
    max_unauthorized_tool_executions: int | None = Field(default=0, ge=0)
    max_harness_errors: int | None = Field(default=0, ge=0)
    # Checks that must have zero failures, and must apply to at least one case.
    required_checks: list[CheckName] = Field(default_factory=list)


class GateRule(StrictModel):
    name: str
    threshold: JsonValue
    actual: JsonValue
    passed: bool
    detail: str = ""


class GateResult(StrictModel):
    passed: bool
    rules: list[GateRule]
    baseline_used: bool = False


class EnvironmentFingerprint(StrictModel):
    agentscope_version: str
    check_engine_version: str
    python_version: str
    langgraph_version: str
    data_hash: str  # hash of the packaged synthetic tool data
    provider: str
    model: str
    deterministic: bool  # True only for the fake provider


class SuiteResult(StrictModel):
    suite_id: str
    label: str = ""
    status: Literal["running", "completed", "error"] = "completed"
    dataset_name: str
    dataset_version: str
    dataset_hash: str
    agent_config: AgentConfig
    config_hash: str
    effective_config_hash: str
    checks_enabled: list[CheckName]
    environment: EnvironmentFingerprint
    started_at: datetime
    finished_at: datetime | None
    cases: list[CaseResult]
    metrics: SuiteMetrics
    gate: GateResult | None = None
