"""Request and response bodies for the HTTP API."""

from __future__ import annotations

from typing import Any

from pydantic import Field

from agentscope.domain.agents import AgentConfig
from agentscope.domain.common import StrictModel
from agentscope.domain.datasets import EvalCase, ExpectedAnswer
from agentscope.domain.evaluation import CaseResult
from agentscope.domain.tools import FaultSpec, ModelFaultSpec, ToolSpec
from agentscope.domain.traces import RunSummary, TraceRecord


class AgentInfo(StrictModel):
    name: str
    title: str
    description: str
    toolset: list[str]
    output_schema: dict[str, Any]
    prompts: dict[str, str]
    versions: list[AgentConfig]


class AgentsResponse(StrictModel):
    agents: list[AgentInfo]
    tools: list[ToolSpec]


class RunCreate(StrictModel):
    """Start a run from a built-in version (`agent_ref`) or a full configuration."""

    agent_ref: str | None = Field(default=None, max_length=128)
    config: AgentConfig | None = None
    task: str = Field(min_length=1, max_length=4000)
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
