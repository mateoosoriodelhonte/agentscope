"""Runs one agent task end to end and returns its trace."""

from __future__ import annotations

import asyncio
import contextlib
import uuid
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from langgraph.errors import GraphRecursionError

from agentscope.agents.base import AgentCatalog
from agentscope.config import ModelRate
from agentscope.domain.agents import AgentConfig, Limits
from agentscope.domain.datasets import LimitsOverride
from agentscope.domain.tools import FaultSpec, ModelFaultSpec
from agentscope.domain.traces import CostEstimate, RunStatus, SpanKind, TokenUsage, TraceRecord
from agentscope.providers.base import ChatMessage, ModelProvider
from agentscope.runtime.context import RunContext
from agentscope.runtime.graph import build_graph, recursion_limit
from agentscope.runtime.model_faults import FaultyModelProvider
from agentscope.runtime.prompts import build_system_prompt
from agentscope.runtime.protocol import decision_schema
from agentscope.tools.base import ToolContext
from agentscope.tools.executor import ToolExecutor
from agentscope.tools.faults import FaultPlan
from agentscope.tools.registry import ToolRegistry
from agentscope.tracing import semconv as sc
from agentscope.tracing.recorder import TraceRecorder, TracingPipeline
from agentscope.tracing.redaction import Redactor


@dataclass(frozen=True)
class RunRequest:
    config: AgentConfig
    task: str
    faults: Sequence[FaultSpec] = ()
    model_faults: Sequence[ModelFaultSpec] = ()
    # Restricts the agent's configured tools further (e.g. a test case's permitted tools).
    permitted_tools: Sequence[str] | None = None
    forbidden_tools: Sequence[str] = ()
    limits: LimitsOverride | None = None
    case_id: str | None = None
    run_id: str = field(default_factory=lambda: str(uuid.uuid4()))


def effective_limits(base: Limits, override: LimitsOverride | None) -> Limits:
    """Per-case overrides can only tighten limits, never loosen them."""
    if override is None:
        return base
    patch: dict[str, Any] = {}
    for name, value in override.model_dump().items():
        if value is not None:
            patch[name] = min(value, getattr(base, name))
    return base.model_copy(update=patch)


def effective_tools(
    configured: Sequence[str], permitted: Sequence[str] | None, forbidden: Sequence[str]
) -> frozenset[str]:
    """A case can narrow the agent's tools but never grant new ones."""
    tools = set(configured)
    if permitted is not None:
        tools &= set(permitted)
    return frozenset(tools - set(forbidden))


def estimate_cost(usage: TokenUsage | None, rate: ModelRate | None) -> CostEstimate | None:
    """Cost only from explicit rates and provider-reported tokens. Never estimated otherwise."""
    if usage is None or rate is None or usage.input_tokens is None or usage.output_tokens is None:
        return None
    amount = (
        usage.input_tokens / 1000 * rate.input_per_1k_tokens
        + usage.output_tokens / 1000 * rate.output_per_1k_tokens
    )
    return CostEstimate(
        amount=round(amount, 8),
        input_per_1k_tokens=rate.input_per_1k_tokens,
        output_per_1k_tokens=rate.output_per_1k_tokens,
        partial=not usage.complete,
    )


@contextlib.contextmanager
def _third_party_tracing_disabled() -> Iterator[None]:
    """Belt and braces on top of agentscope._bootstrap: LangSmith tracing off for the run."""
    try:
        from langsmith.run_helpers import tracing_context
    except ImportError:  # pragma: no cover - langsmith is an optional langgraph dependency
        yield
        return
    with tracing_context(enabled=False):
        yield


class AgentRunner:
    def __init__(
        self,
        catalog: AgentCatalog,
        tools: ToolRegistry,
        providers: dict[str, ModelProvider],
        *,
        redactor: Redactor | None = None,
        model_rates: dict[str, ModelRate] | None = None,
        pipeline: TracingPipeline | None = None,
    ) -> None:
        self.catalog = catalog
        self.tools = tools
        self._providers = providers
        self.redactor = redactor or Redactor()
        self._rates = model_rates or {}
        self._pipeline = pipeline

    def provider(self, name: str) -> ModelProvider:
        try:
            return self._providers[name]
        except KeyError:
            raise LookupError(
                f"model provider {name!r} is not enabled; enabled: {sorted(self._providers)}"
            ) from None

    def effective_config_hash(
        self, config: AgentConfig, permitted: Sequence[str] | None = None
    ) -> str:
        tools = effective_tools(config.tools, permitted, ())
        specs = self.tools.specs(sorted(tools), config.tool_schema_version)
        return self.catalog.get(config.agent).effective_config_hash(config, specs)

    async def run(self, request: RunRequest) -> TraceRecord:
        config = request.config
        self.catalog.validate(config)
        definition = self.catalog.get(config.agent)
        limits = effective_limits(config.limits, request.limits)
        permitted = effective_tools(config.tools, request.permitted_tools, request.forbidden_tools)
        specs = self.tools.specs(sorted(permitted), config.tool_schema_version)
        output_schema = definition.output_schema()
        effective_hash = definition.effective_config_hash(config, specs)
        base_provider = self.provider(config.model.provider.value)
        provider: ModelProvider = (
            FaultyModelProvider(base_provider, request.model_faults)
            if request.model_faults
            else base_provider
        )
        provider_label = "agentscope.fake" if base_provider.name == "fake" else base_provider.name
        system_prompt = build_system_prompt(
            definition.prompt(config.prompt_version), specs, output_schema
        )

        recorder = TraceRecorder(self.redactor, self._pipeline)
        started = datetime.now(UTC)
        status = RunStatus.ERROR
        error_code: str | None = "internal_error"
        error: str | None = None
        last: dict[str, Any] = {}

        with (
            _third_party_tracing_disabled(),
            recorder.span(
                f"invoke_agent {config.agent}",
                SpanKind.RUN,
                attributes={
                    sc.GEN_AI_OPERATION_NAME: "invoke_agent",
                    sc.GEN_AI_AGENT_NAME: config.agent,
                    sc.GEN_AI_PROVIDER_NAME: provider_label,
                    sc.GEN_AI_REQUEST_MODEL: config.model.model,
                    sc.AS_AGENT_VERSION: config.version,
                    sc.AS_CONFIG_HASH: config.config_hash(),
                    sc.AS_EFFECTIVE_CONFIG_HASH: effective_hash,
                    sc.AS_RUN_ID: request.run_id,
                    sc.AS_CASE_ID: request.case_id,
                    sc.AS_FAULTS: len(request.faults) + len(request.model_faults),
                },
                input={
                    "task": request.task,
                    "faults": [f.model_dump(mode="json") for f in request.faults],
                    "model_faults": [f.model_dump(mode="json") for f in request.model_faults],
                },
            ) as run_span,
        ):
            ctx = RunContext(
                run_id=request.run_id,
                config=config,
                limits=limits,
                permitted=permitted,
                tool_specs=specs,
                output_schema=output_schema,
                decision_schema=decision_schema(output_schema),
                provider=provider,
                provider_name=provider_label,
                executor=ToolExecutor(
                    self.tools,
                    ToolContext(run_id=request.run_id),
                    fault_plan=FaultPlan(request.faults),
                    schema_version=config.tool_schema_version,
                    timeout_s=limits.tool_timeout_s,
                    max_output_chars=limits.max_tool_output_chars,
                ),
                recorder=recorder,
                run_span=run_span,
            )
            graph = build_graph(ctx)
            initial: dict[str, Any] = {
                "step": 0,
                "messages": [
                    ChatMessage(role="system", content=system_prompt),
                    ChatMessage(role="user", content=request.task),
                ],
                "decision_repairs": 0,
                "output_repairs": 0,
                "pending": None,
                "result": None,
                "candidate": None,
                "status": None,
                "error_code": None,
                "error": None,
            }
            recorder.transition("START", "plan", 0, "run started")
            limit = recursion_limit(
                limits.max_steps, config.retry.max_decision_repairs, config.retry.max_output_repairs
            )
            try:
                async with asyncio.timeout(limits.run_timeout_s):
                    # Stream full state snapshots so a run-level timeout keeps partial state.
                    async for snapshot in graph.astream(
                        initial,
                        config={"recursion_limit": limit, "callbacks": []},
                        stream_mode="values",
                    ):
                        last = snapshot
                status = RunStatus(last.get("status") or RunStatus.ERROR.value)
                error_code = last.get("error_code")
                error = last.get("error")
            except TimeoutError:
                status, error_code = RunStatus.TIMEOUT, "run_timeout"
                error = f"run exceeded {limits.run_timeout_s:g}s"
                recorder.transition(ctx.last_node, "END", ctx.step, "run deadline passed")
            except GraphRecursionError:
                status, error_code = RunStatus.ERROR, "internal_recursion_limit"
                error = "graph recursion backstop reached"
                recorder.transition(ctx.last_node, "END", ctx.step, "recursion backstop")
            except Exception as exc:  # never leak tracebacks into stored traces
                status, error_code = RunStatus.ERROR, "internal_error"
                error = f"runtime error: {type(exc).__name__}"
                recorder.transition(ctx.last_node, "END", ctx.step, "runtime error")
            final_output = (
                last.get("candidate") if status in (RunStatus.COMPLETED, RunStatus.FAILED) else None
            )
            run_span.set_attributes({sc.AS_RUN_STATUS: status.value, sc.AS_RUN_STEPS: ctx.step})
            run_span.set_output(
                {"status": status.value, "final_output": final_output, "error": error}
            )
            if status is RunStatus.COMPLETED:
                run_span.ok()
            else:
                run_span.fail(error or status.value)

        spans = recorder.finish()
        ended = datetime.now(UTC)
        usage = ctx.usage.result()
        redact = self.redactor.redact
        return TraceRecord(
            trace_id=run_span.trace_id,
            run_id=request.run_id,
            agent=config.agent,
            agent_version=config.version,
            config_hash=config.config_hash(),
            effective_config_hash=effective_hash,
            config=config,
            limits=limits,
            permitted_tools=sorted(permitted),
            task=self.redactor.redact_text(request.task),
            status=status,
            error_code=error_code if status is not RunStatus.COMPLETED else None,
            error=self.redactor.redact_text(error) if error else None,
            started_at=started,
            ended_at=ended,
            duration_ms=(ended - started).total_seconds() * 1000,
            steps=ctx.step,
            provider=provider_label,
            model=config.model.model,
            final_output=redact(final_output),
            usage=usage,
            cost=estimate_cost(usage, self._rates.get(config.model.model)),
            faults=[f.model_copy(update={"payload": redact(f.payload)}) for f in request.faults],
            model_faults=list(request.model_faults),
            case_id=request.case_id,
            spans=spans,
            transitions=recorder.transitions,
            tool_invocations=[
                inv.model_copy(
                    update={"arguments": redact(inv.arguments), "output": redact(inv.output)}
                )
                for inv in ctx.invocations
            ],
            guardrail_events=ctx.guardrails,
        )
