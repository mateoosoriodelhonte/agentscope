"""Every edge of the agent state machine, driven by scripted decisions."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from agentscope.agents.base import AgentCatalog, AgentDefinition
from agentscope.domain.agents import AgentConfig, GraphVariant, Limits, ModelConfig, RetryPolicy
from agentscope.domain.datasets import LimitsOverride
from agentscope.domain.tools import (
    FaultKind,
    FaultSpec,
    ModelFaultKind,
    ModelFaultSpec,
    ToolOutcome,
)
from agentscope.domain.traces import RunStatus, TraceRecord
from agentscope.providers.base import ModelRequest, ModelResponse, ProviderError
from agentscope.providers.fake import ConversationView, FakeModelProvider
from agentscope.runtime.graph import topology
from agentscope.runtime.runner import AgentRunner, RunRequest
from agentscope.tools.registry import ToolRegistry
from agentscope.tracing import semconv as sc


class Out(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["answered", "insufficient_evidence"]
    answer: str


SEARCH = {
    "type": "tool_call",
    "tool": "search_documents",
    "arguments": {"query": "ferry"},
    "rationale": "",
}
FINAL = {"type": "final", "output": {"status": "answered", "answer": "25"}, "rationale": ""}
BAD_FINAL = {"type": "final", "output": {"answer": 25}, "rationale": ""}


class Scripted:
    """Returns decisions in order; the last one repeats."""

    def __init__(self, decisions: Sequence[dict[str, Any] | str]) -> None:
        self.decisions = list(decisions)
        self.calls = 0

    def decide(self, view: ConversationView) -> dict[str, Any]:
        item = self.decisions[min(self.calls, len(self.decisions) - 1)]
        self.calls += 1
        if isinstance(item, str):
            raise _RawText(item)
        return item


class _RawText(Exception):
    def __init__(self, text: str) -> None:
        self.text = text


class ScriptedProvider(FakeModelProvider):
    """Lets a script return raw (possibly invalid) text."""

    async def generate(self, request: ModelRequest) -> ModelResponse:
        try:
            return await super().generate(request)
        except _RawText as raw:
            if raw.text == "PROVIDER_DOWN":
                raise ProviderError("connection refused") from None
            if raw.text == "SLOW":
                await asyncio.sleep(30)  # outlasts any run deadline used here
            return ModelResponse(content=raw.text, provider="fake", model=request.config.model)


def make_runner(
    registry: ToolRegistry, script: Sequence[dict[str, Any] | str], **config: Any
) -> tuple[AgentRunner, AgentConfig]:
    brain = Scripted(script)
    agent_config = AgentConfig(
        agent="scripted",
        version="t",
        prompt_version="p",
        model=ModelConfig(model="scripted-careful"),
        tools=["search_documents", "retrieve_document"],
        **config,
    )
    definition = AgentDefinition(
        name="scripted",
        title="Scripted",
        description="test",
        toolset=("search_documents", "retrieve_document"),
        output_model=Out,
        prompts={"p": "Test prompt."},
        versions=(agent_config,),
        brain=brain,
    )
    catalog = AgentCatalog([definition])
    return AgentRunner(
        catalog, registry, {"fake": ScriptedProvider(catalog.brains())}
    ), agent_config


def edges(trace: TraceRecord) -> list[tuple[str, str]]:
    return [(t.source, t.target) for t in trace.transitions]


async def run(
    registry: ToolRegistry,
    script: Sequence[dict[str, Any] | str],
    *,
    faults: Sequence[FaultSpec] = (),
    model_faults: Sequence[ModelFaultSpec] = (),
    limits: LimitsOverride | None = None,
    **config: Any,
) -> TraceRecord:
    runner, agent_config = make_runner(registry, script, **config)
    return await runner.run(
        RunRequest(
            config=agent_config,
            task="How long is the ferry crossing?",
            faults=faults,
            model_faults=model_faults,
            limits=limits,
        )
    )


async def test_happy_path_basic(registry: ToolRegistry) -> None:
    trace = await run(registry, [SEARCH, FINAL])
    assert trace.status is RunStatus.COMPLETED
    assert edges(trace) == [
        ("START", "plan"),
        ("plan", "guard"),
        ("guard", "act"),
        ("act", "observe"),
        ("observe", "plan"),
        ("plan", "finish"),
        ("finish", "END"),
    ]
    assert trace.final_output == FINAL["output"]
    assert trace.usage is None  # the fake provider reports nothing, so nothing is shown


async def test_denied_tool_goes_to_observe_without_executing(registry: ToolRegistry) -> None:
    trace = await run(
        registry,
        [{"type": "tool_call", "tool": "send_email", "arguments": {}, "rationale": ""}, FINAL],
    )
    assert ("guard", "observe") in edges(trace)
    (inv,) = trace.tool_invocations
    assert (inv.permitted, inv.executed, inv.outcome) == (
        False,
        False,
        ToolOutcome.PERMISSION_DENIED,
    )
    assert [g.kind for g in trace.guardrail_events] == ["tool_denied"]


async def test_invalid_arguments_are_blocked_but_permitted(registry: ToolRegistry) -> None:
    trace = await run(
        registry,
        [
            {
                "type": "tool_call",
                "tool": "search_documents",
                "arguments": {"query": 7},
                "rationale": "",
            },
            FINAL,
        ],
    )
    (inv,) = trace.tool_invocations
    assert (inv.permitted, inv.executed, inv.outcome) == (
        True,
        False,
        ToolOutcome.INVALID_ARGUMENTS,
    )


async def test_invalid_decision_with_repair_budget(registry: ToolRegistry) -> None:
    trace = await run(
        registry, ["not json at all", FINAL], retry=RetryPolicy(max_decision_repairs=1)
    )
    assert ("plan", "plan") in edges(trace)
    assert trace.status is RunStatus.COMPLETED
    assert [g.kind for g in trace.guardrail_events] == ["invalid_decision", "decision_repaired"]


async def test_invalid_decision_without_budget_fails(registry: ToolRegistry) -> None:
    trace = await run(registry, ["{oops"])
    assert trace.status is RunStatus.FAILED
    assert trace.error_code == "invalid_decision"
    assert trace.final_output is None


async def test_validated_graph_repairs_invalid_output(registry: ToolRegistry) -> None:
    trace = await run(
        registry,
        [BAD_FINAL, FINAL],
        graph=GraphVariant.VALIDATED,
        retry=RetryPolicy(max_output_repairs=1),
    )
    assert ("validate", "plan") in edges(trace)
    assert ("validate", "finish") in edges(trace)
    assert trace.status is RunStatus.COMPLETED
    assert "output_repaired" in [g.kind for g in trace.guardrail_events]


async def test_validated_graph_fails_when_repairs_exhausted(registry: ToolRegistry) -> None:
    trace = await run(registry, [BAD_FINAL], graph=GraphVariant.VALIDATED)
    assert trace.status is RunStatus.FAILED
    assert trace.error_code == "invalid_output"
    assert trace.final_output == BAD_FINAL["output"]  # kept so evaluators can inspect it


async def test_basic_graph_accepts_invalid_output_as_is(registry: ToolRegistry) -> None:
    trace = await run(registry, [BAD_FINAL])
    assert trace.status is RunStatus.COMPLETED  # the evaluator, not the runtime, flags it


async def test_step_budget(registry: ToolRegistry) -> None:
    trace = await run(registry, [SEARCH], limits=LimitsOverride(max_steps=3))
    assert trace.status is RunStatus.MAX_STEPS
    assert trace.steps == 3
    assert edges(trace)[-2:] == [("plan", "finish"), ("finish", "END")]


async def test_case_limits_only_tighten(registry: ToolRegistry) -> None:
    runner, agent_config = make_runner(registry, [SEARCH], limits=Limits(max_steps=2))
    trace = await runner.run(
        RunRequest(config=agent_config, task="t", limits=LimitsOverride(max_steps=50))
    )
    assert trace.limits.max_steps == 2
    assert trace.steps == 2


async def test_runtime_retry_recovers_a_transient_fault(registry: ToolRegistry) -> None:
    trace = await run(
        registry,
        [SEARCH, FINAL],
        faults=[FaultSpec(tool="search_documents", kind=FaultKind.ERROR)],
        retry=RetryPolicy(max_tool_retries=1),
    )
    (inv,) = trace.tool_invocations
    assert inv.outcome is ToolOutcome.OK
    assert [a.outcome for a in inv.attempts] == [ToolOutcome.ERROR, ToolOutcome.OK]
    assert len([s for s in trace.spans if s.kind.value == "tool"]) == 2


async def test_an_injection_reaches_the_first_call_with_output_to_carry_it(
    registry: ToolRegistry,
) -> None:
    no_hits = {**SEARCH, "arguments": {"query": "zzzz qqqq"}}
    trace = await run(
        registry,
        [no_hits, SEARCH, FINAL],
        faults=[FaultSpec(tool="search_documents", kind=FaultKind.INJECTION, on_call=1)],
    )
    first, second = trace.tool_invocations
    assert [a.fault for a in first.attempts] == [None]  # nothing to inject into
    assert [a.fault for a in second.attempts] == [FaultKind.INJECTION]
    spans = {s.span_id: s for s in trace.spans}
    assert spans[first.attempts[0].span_id].attributes[sc.AS_FAULT_UNDELIVERED] == "injection"
    assert sc.AS_FAULT_UNDELIVERED not in spans[second.attempts[0].span_id].attributes


async def test_tool_timeout_is_an_outcome_not_a_run_status(registry: ToolRegistry) -> None:
    trace = await run(
        registry,
        [SEARCH, FINAL],
        faults=[FaultSpec(tool="search_documents", kind=FaultKind.TIMEOUT)],
        limits=LimitsOverride(tool_timeout_s=0.05),
    )
    assert trace.status is RunStatus.COMPLETED
    assert trace.tool_invocations[0].outcome is ToolOutcome.TIMEOUT


async def test_run_deadline_keeps_partial_trace(registry: ToolRegistry) -> None:
    trace = await run(
        registry,
        [SEARCH],
        faults=[FaultSpec(tool="search_documents", kind=FaultKind.TIMEOUT, on_call=None)],
        limits=LimitsOverride(tool_timeout_s=0.2, run_timeout_s=0.3),
        retry=RetryPolicy(max_tool_retries=3),
    )
    assert trace.status is RunStatus.TIMEOUT
    assert trace.error_code == "run_timeout"
    assert edges(trace)[0] == ("START", "plan")
    assert edges(trace)[-1][1] == "END"
    cancelled = [s for s in trace.spans if s.attributes.get("agentscope.cancelled")]
    assert cancelled, "spans interrupted by the deadline must be marked cancelled"


async def test_run_deadline_keeps_the_tool_call_it_interrupted(registry: ToolRegistry) -> None:
    trace = await run(
        registry,
        [SEARCH],
        faults=[FaultSpec(tool="search_documents", kind=FaultKind.TIMEOUT, on_call=None)],
        limits=LimitsOverride(run_timeout_s=0.3),  # the hang outlasts it (tool timeout 2s)
    )
    assert trace.status is RunStatus.TIMEOUT
    (inv,) = trace.tool_invocations
    assert (inv.tool, inv.permitted, inv.executed) == ("search_documents", True, True)
    assert inv.outcome is ToolOutcome.TIMEOUT
    assert inv.arguments == SEARCH["arguments"]
    [attempt] = inv.attempts
    assert (attempt.outcome, attempt.fault) == (ToolOutcome.TIMEOUT, FaultKind.TIMEOUT)
    assert "run deadline" in (attempt.error or "")
    spans = {s.span_id: s for s in trace.spans}
    assert spans[attempt.span_id].attributes.get(sc.AS_CANCELLED) is True
    assert edges(trace)[-1] == ("act", "END")  # act was running, not guard


async def test_run_deadline_during_retries_keeps_every_attempt(registry: ToolRegistry) -> None:
    trace = await run(
        registry,
        [SEARCH],
        faults=[FaultSpec(tool="search_documents", kind=FaultKind.TIMEOUT, on_call=None)],
        limits=LimitsOverride(tool_timeout_s=0.2, run_timeout_s=0.3),
        retry=RetryPolicy(max_tool_retries=3),
    )
    (inv,) = trace.tool_invocations
    assert [a.attempt for a in inv.attempts] == [1, 2]
    assert [a.outcome for a in inv.attempts] == [ToolOutcome.TIMEOUT, ToolOutcome.TIMEOUT]
    assert "run deadline" in (inv.attempts[1].error or "")


async def test_run_deadline_during_a_model_call_names_the_plan_node(
    registry: ToolRegistry,
) -> None:
    trace = await run(registry, [SEARCH, "SLOW"], limits=LimitsOverride(run_timeout_s=0.3))
    assert trace.status is RunStatus.TIMEOUT
    assert edges(trace)[-2:] == [("observe", "plan"), ("plan", "END")]
    assert [i.outcome for i in trace.tool_invocations] == [ToolOutcome.OK]


async def test_failed_spans_carry_an_exportable_error_type(registry: ToolRegistry) -> None:
    missing = {
        "type": "tool_call",
        "tool": "retrieve_document",
        "arguments": {"doc_id": "doc-acme-takeover-plan"},
        "rationale": "",
    }
    trace = await run(registry, [missing, "PROVIDER_DOWN"])
    by_kind = {s.kind.value: s for s in trace.spans if s.status.value == "error"}
    assert by_kind["tool"].attributes[sc.ERROR_TYPE] == "error"
    assert "doc-acme-takeover-plan" in (by_kind["tool"].status_message or "")  # local only
    assert by_kind["llm"].attributes[sc.ERROR_TYPE] == "provider_error"
    assert by_kind["run"].attributes[sc.ERROR_TYPE] == "provider_error"


async def test_provider_failure(registry: ToolRegistry) -> None:
    trace = await run(registry, ["PROVIDER_DOWN"])
    assert trace.status is RunStatus.ERROR
    assert trace.error_code == "provider_error"


async def test_model_fault_injection_exercises_repair(registry: ToolRegistry) -> None:
    trace = await run(
        registry,
        [SEARCH, FINAL],
        model_faults=[ModelFaultSpec(kind=ModelFaultKind.INVALID_JSON)],
        retry=RetryPolicy(max_decision_repairs=1),
    )
    kinds = [g.kind for g in trace.guardrail_events]
    assert kinds[:2] == ["model_fault_injected", "invalid_decision"]
    assert trace.status is RunStatus.COMPLETED


async def test_unknown_tool_model_fault(registry: ToolRegistry) -> None:
    trace = await run(
        registry, [SEARCH, FINAL], model_faults=[ModelFaultSpec(kind=ModelFaultKind.UNKNOWN_TOOL)]
    )
    blocked = trace.tool_invocations[0]
    assert (blocked.tool, blocked.outcome) == ("nonexistent_tool", ToolOutcome.UNKNOWN_TOOL)
    assert (blocked.permitted, blocked.executed) == (False, False)
    kinds = [g.kind for g in trace.guardrail_events]
    assert "unknown_tool" in kinds
    assert "tool_denied" not in kinds  # not an unauthorized attempt by the agent


async def test_an_existing_tool_outside_the_permitted_set_is_still_denied(
    registry: ToolRegistry,
) -> None:
    calc = {"type": "tool_call", "tool": "calculate", "arguments": {}, "rationale": ""}
    trace = await run(registry, [calc, FINAL])
    (inv,) = trace.tool_invocations
    assert inv.outcome is ToolOutcome.PERMISSION_DENIED
    assert [g.kind for g in trace.guardrail_events] == ["tool_denied"]


async def test_concurrent_runs_never_share_spans(registry: ToolRegistry) -> None:
    traces = await asyncio.gather(*(run(registry, [SEARCH, FINAL]) for _ in range(6)))
    for trace in traces:
        assert {s.trace_id for s in trace.spans} == {trace.trace_id}
        assert sum(1 for s in trace.spans if s.parent_span_id is None) == 1
    assert len({t.trace_id for t in traces}) == 6


async def test_every_graph_edge_is_exercised(registry: ToolRegistry) -> None:
    denied = {"type": "tool_call", "tool": "send_email", "arguments": {}, "rationale": ""}
    scenarios: list[tuple[list[Any], dict[str, Any]]] = [
        ([SEARCH, FINAL], {}),
        ([denied, FINAL], {}),
        (["bad", FINAL], {"retry": RetryPolicy(max_decision_repairs=1)}),
        (
            [BAD_FINAL, FINAL],
            {"graph": GraphVariant.VALIDATED, "retry": RetryPolicy(max_output_repairs=1)},
        ),
    ]
    seen: set[tuple[str, str]] = set()
    for script, config in scenarios:
        seen.update(edges(await run(registry, script, **config)))
    assert set(topology(GraphVariant.VALIDATED)) <= seen
