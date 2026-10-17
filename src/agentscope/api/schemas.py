"""Request and response bodies for the HTTP API — the contract the dashboard is built on.

Long-running work (runs, evaluations, experiments, demo seeding) is started with a POST
that returns 202 and a job id; clients poll `GET /api/jobs/{job_id}` and then fetch the
result resource named in the job.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import Field

from agentscope.domain.agents import AgentConfig
from agentscope.domain.common import Slug, StorableText, StrictModel
from agentscope.domain.datasets import Dataset, EvalCase, ExpectedAnswer
from agentscope.domain.evaluation import CaseResult, GateResult, GateThresholds
from agentscope.domain.tools import FaultSpec, ModelFaultSpec, ToolSpec
from agentscope.domain.traces import RunSummary, TraceRecord

# --- agents, tools, providers --------------------------------------------------------


class ConfigHashes(StrictModel):
    """The fingerprints a suite of this version records, so clients match suites to built-in
    versions exactly instead of guessing from labels."""

    config_hash: str
    effective_config_hash: str


class AgentInfo(StrictModel):
    name: str
    title: str
    description: str
    toolset: list[str]
    output_schema: dict[str, Any]
    prompts: dict[str, str]
    versions: list[AgentConfig]
    # Keyed by AgentConfig.version.
    version_hashes: dict[str, ConfigHashes] = Field(default_factory=dict)


class AgentsResponse(StrictModel):
    agents: list[AgentInfo]
    tools: list[ToolSpec]


class ProviderStatus(StrictModel):
    name: str
    enabled: bool
    deterministic: bool
    # None = not probed (disabled); True/False = probe result against the configured target.
    reachable: bool | None = None
    target: str | None = None  # host:port only, never credentials
    models: list[str] = Field(default_factory=list)
    detail: str = ""


# --- jobs --------------------------------------------------------------------------------

JobKind = Literal["run", "evaluation", "experiment", "demo_seed"]
JobState = Literal["queued", "running", "succeeded", "failed"]


class JobAccepted(StrictModel):
    job_id: str
    kind: JobKind
    # The id of the resource that will exist when the job succeeds.
    resource_id: str


class JobStatus(StrictModel):
    job_id: str
    kind: JobKind
    state: JobState
    resource_id: str
    created_at: datetime
    finished_at: datetime | None = None
    progress: int = 0  # cases completed (evaluations/experiments)
    total: int = 0
    error: str | None = None


# --- runs -------------------------------------------------------------------------------


class AgentSelector(StrictModel):
    """A built-in version (`agent_ref`, e.g. `research_assistant@v2`) or a full config."""

    agent_ref: str | None = Field(default=None, max_length=128)
    config: AgentConfig | None = None


class RunCreate(AgentSelector):
    task: StorableText = Field(min_length=1, max_length=4000)
    faults: list[FaultSpec] = Field(default_factory=list, max_length=10)
    model_faults: list[ModelFaultSpec] = Field(default_factory=list, max_length=10)
    # Optional expectation: the run is then evaluated as an ad-hoc test case.
    expected: ExpectedAnswer | None = None


class RunDetail(StrictModel):
    trace: TraceRecord
    case: EvalCase | None = None
    evaluation: CaseResult | None = None


class RunList(StrictModel):
    items: list[RunSummary]
    total: int


# --- datasets ---------------------------------------------------------------------------


class DatasetSummary(StrictModel):
    name: str
    version: str
    content_hash: str
    description: str
    agent: str | None
    case_count: int
    tags: list[str]
    created_at: datetime


class DatasetDetail(StrictModel):
    summary: DatasetSummary
    # In GET /api/datasets/{name}/{version}, the export document (see routes/datasets.py).
    dataset: Dataset


class ValidationIssue(StrictModel):
    path: str  # JSON-pointer-like location, e.g. /cases/3/expected/value
    message: str
    severity: Literal["error", "warning"] = "error"


class DatasetValidation(StrictModel):
    valid: bool
    issues: list[ValidationIssue]
    content_hash: str | None = None
    case_count: int = 0


# --- evaluations ------------------------------------------------------------------------


class DatasetRef(StrictModel):
    name: Slug
    version: Slug


class EvaluationCreate(AgentSelector):
    dataset: DatasetRef
    label: str = Field(default="", max_length=200)
    thresholds: GateThresholds | None = None


class SuiteListItem(StrictModel):
    suite_id: str
    label: str
    status: Literal["running", "completed", "error"]
    dataset_name: str
    dataset_version: str
    dataset_hash: str
    agent_ref: str
    config_hash: str
    provider: str
    model: str
    deterministic: bool
    started_at: datetime
    finished_at: datetime | None
    total: int
    passed: int
    pass_rate: float
    gate_passed: bool | None


# --- experiments ------------------------------------------------------------------------


class ExperimentCreate(StrictModel):
    """Either run both sides now (`dataset` + `a` + `b`) or compare two finished suites."""

    name: str = Field(min_length=1, max_length=200)
    dataset: DatasetRef | None = None
    a: AgentSelector | None = None
    b: AgentSelector | None = None
    suite_a: str | None = None
    suite_b: str | None = None


class ExperimentListItem(StrictModel):
    experiment_id: str
    name: str
    created_at: datetime
    dataset_name: str
    dataset_version: str
    a_ref: str
    b_ref: str
    a_pass_rate: float
    b_pass_rate: float
    regressions: int
    fixes: int
    confounded: bool
    deterministic: bool


# --- failure analysis -------------------------------------------------------------------


class ResilienceRow(StrictModel):
    """How one agent version handled cases with one fault kind injected."""

    agent_ref: str
    fault_kind: str  # a FaultKind or ModelFaultKind value
    cases: int
    passed: int
    recovered: int  # error_recovery passed via recovery
    degraded: int  # error_recovery passed via graceful degradation
    failed: int


class FailureBucket(StrictModel):
    check: str
    reason: str
    fault_kind: str | None
    run_status: str
    count: int
    example_run_ids: list[str]


class InjectionSummary(StrictModel):
    agent_ref: str
    cases: int
    # Simulated susceptibility when the provider is the fake model; real behaviour otherwise.
    unauthorized_attempts: int
    # Enforced by the runtime permission guard — must always be 0.
    unauthorized_executions: int
    leaked_cases: int  # policy_compliance failed with leaked_content
    deterministic: bool


class FailureAnalysis(StrictModel):
    suites_considered: list[str]
    resilience: list[ResilienceRow]
    buckets: list[FailureBucket]
    injection: list[InjectionSummary]


# --- overview ---------------------------------------------------------------------------


class PassRatePoint(StrictModel):
    suite_id: str
    finished_at: datetime
    pass_rate: float
    dataset: str
    gate_passed: bool | None


class PassRateSeries(StrictModel):
    agent_ref: str
    points: list[PassRatePoint]


class Overview(StrictModel):
    counts: dict[str, int]  # runs, suites, datasets, experiments
    recent_runs: list[RunSummary]
    recent_suites: list[SuiteListItem]
    pass_rate_series: list[PassRateSeries]
    latest_gate: GateResult | None
    providers: list[ProviderStatus]


# --- settings and data ------------------------------------------------------------------


class TableInventory(StrictModel):
    name: str
    rows: int
    description: str


class StorageInventory(StrictModel):
    backend: Literal["sqlite", "postgresql"]
    location: str  # file path for SQLite; host/db for PostgreSQL (no credentials)
    size_bytes: int | None
    tables: list[TableInventory]


class SettingsView(StrictModel):
    version: str
    effective: dict[str, Any]  # redacted effective settings
    storage: StorageInventory
    egress: list[str]  # every network destination AgentScope may contact
    privacy: list[str]  # plain-language statements of what is and is not collected


class PurgeRequest(StrictModel):
    confirm: Literal["PURGE"]
    scope: Literal["all", "runs", "evaluations", "experiments", "datasets"] = "all"
    before: datetime | None = None


class PurgeResult(StrictModel):
    deleted: dict[str, int]
    size_bytes_before: int | None
    size_bytes_after: int | None


PurgeConflictCode = Literal["jobs_running", "purge_incomplete"]


class PurgeConflict(StrictModel):
    """409 from POST /api/settings/purge. `jobs_running`: refused, nothing was deleted (no
    result). `purge_incomplete`: rows were deleted (see result) but their old contents are
    still on disk until a later purge succeeds."""

    detail: str
    code: PurgeConflictCode
    result: PurgeResult | None = None
