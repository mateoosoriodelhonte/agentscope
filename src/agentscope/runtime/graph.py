"""The agent state machine, expressed as a LangGraph StateGraph.

Edge table (every edge is recorded as a state transition with a reason):

    START    → plan
    plan     → guard      the model requested a tool
    plan     → validate   final answer (validated graph)
    plan     → finish     final answer (basic graph) | step budget exhausted (max_steps)
                          | decision repairs exhausted (failed) | provider failure (error)
    plan     → plan       invalid decision, a decision repair remains
    guard    → act        permitted tool, valid arguments
    guard    → observe    denied / unknown tool / invalid arguments (nothing executes)
    act      → observe    (runtime retries happen inside act, one span per attempt)
    observe  → plan
    validate → finish     output valid (completed) | repairs exhausted (failed)
    validate → plan       output invalid, an output repair remains
    finish   → END

Control flow lives in the graph state; nodes are pure with respect to routing (the router
only reads `route`). Append-only trace records (tool invocations, guardrail events) live on
the RunContext so they survive a run-level timeout.
"""

from __future__ import annotations

import asyncio
import operator
import time
from collections.abc import Awaitable, Callable
from typing import Annotated, Any, TypedDict

from jsonschema import Draft202012Validator
from langgraph.graph import END, START, StateGraph

from agentscope.domain.agents import GraphVariant
from agentscope.domain.tools import ToolCall, ToolOutcome, ToolResult
from agentscope.domain.traces import (
    GuardrailKind,
    RunStatus,
    SpanKind,
    ToolAttempt,
    ToolInvocation,
)
from agentscope.providers.base import ChatMessage, ModelRequest, ModelResponse, ProviderError
from agentscope.runtime.context import RunContext
from agentscope.runtime.guards import check_permission, scan_untrusted
from agentscope.runtime.prompts import RESPONSE_FORMAT, render_observation
from agentscope.runtime.protocol import DecisionError, ToolCallDecision, parse_decision
from agentscope.tracing import semconv as sc
from agentscope.tracing.recorder import SpanHandle


class AgentState(TypedDict, total=False):
    step: int
    messages: Annotated[list[ChatMessage], operator.add]
    pending: dict[str, Any] | None
    result: dict[str, Any] | None
    candidate: dict[str, Any] | None
    decision_repairs: int
    output_repairs: int
    status: str | None
    error_code: str | None
    error: str | None
    route: str
    reason: str


NodeFn = Callable[[AgentState, SpanHandle], Awaitable[dict[str, Any]]]


def output_errors(output: Any, schema: dict[str, Any], limit: int = 5) -> list[str]:
    """Schema violations as `location: validator` — never the offending values."""
    validator = Draft202012Validator(schema)
    errors = sorted(validator.iter_errors(output), key=lambda e: list(e.absolute_path))
    return [
        f"{'.'.join(str(p) for p in error.absolute_path) or '(root)'}: {error.validator}"
        for error in errors[:limit]
    ]


def build_graph(ctx: RunContext) -> Any:
    validated = ctx.config.graph is GraphVariant.VALIDATED
    limits = ctx.limits
    retry = ctx.config.retry
    model = ctx.config.model

    def node(name: str, fn: NodeFn) -> Callable[[AgentState], Awaitable[dict[str, Any]]]:
        async def wrapped(state: AgentState) -> dict[str, Any]:
            step = state.get("step", 0)
            # Set on entry: a run deadline or error that interrupts this node ends the run
            # from here, not from the node before it.
            ctx.current_node = name
            with ctx.recorder.span(
                f"node {name}",
                SpanKind.NODE,
                parent=ctx.run_span,
                attributes={sc.AS_NODE: name, sc.AS_STEP: step},
            ) as span:
                update = await fn(state, span)
                span.set_attributes({sc.AS_ROUTE: update["route"]})
                span.ok()
            ctx.step = update.get("step", step)
            target = "END" if update["route"] == END else update["route"]
            ctx.recorder.transition(name, target, ctx.step, update["reason"])
            return update

        return wrapped

    def end(
        status: RunStatus, code: str | None, reason: str, error: str | None = None
    ) -> dict[str, Any]:
        return {
            "route": "finish",
            "reason": reason,
            "status": status.value,
            "error_code": code,
            "error": error,
        }

    async def plan(state: AgentState, span: SpanHandle) -> dict[str, Any]:
        step = state.get("step", 0)
        if step >= limits.max_steps:
            ctx.guardrail("max_steps", step, f"step budget of {limits.max_steps} exhausted")
            return end(
                RunStatus.MAX_STEPS,
                "max_steps",
                "step budget exhausted",
                f"no final answer within {limits.max_steps} steps",
            )
        request = ModelRequest(
            agent=ctx.config.agent,
            messages=state.get("messages", []),
            tools=ctx.tool_specs,
            response_schema=ctx.decision_schema,
            config=model,
        )
        with ctx.recorder.span(
            f"chat {model.model}",
            SpanKind.LLM,
            parent=span,
            attributes={
                sc.GEN_AI_OPERATION_NAME: "chat",
                sc.GEN_AI_PROVIDER_NAME: ctx.provider_name,
                sc.GEN_AI_REQUEST_MODEL: model.model,
                sc.GEN_AI_REQUEST_TEMPERATURE: model.temperature,
                sc.GEN_AI_REQUEST_SEED: model.seed,
                sc.GEN_AI_REQUEST_MAX_TOKENS: model.max_output_tokens,
                sc.AS_STEP: step + 1,
            },
            input={"messages": [{"role": m.role, "content": m.content} for m in request.messages]},
        ) as llm:
            response: ModelResponse | None = None
            failure: tuple[str, str] | None = None
            try:
                async with asyncio.timeout(model.request_timeout_s):
                    response = await ctx.provider.generate(request)
            except TimeoutError:
                failure = (
                    "provider_timeout",
                    f"model did not respond within {model.request_timeout_s:g}s",
                )
            except ProviderError as exc:
                failure = ("provider_error", f"provider error: {exc}"[:500])
            if response is None:
                assert failure is not None
                llm.fail(failure[1], error_type=failure[0])
                ctx.usage.record(None)
                return {
                    "step": step + 1,
                    **end(RunStatus.ERROR, failure[0], "model provider failed", failure[1]),
                }
            ctx.usage.record(response.usage)
            llm.set_attributes(
                {
                    sc.GEN_AI_RESPONSE_MODEL: response.model,
                    sc.GEN_AI_RESPONSE_FINISH_REASONS: [response.finish_reason]
                    if response.finish_reason
                    else None,
                    sc.GEN_AI_USAGE_INPUT_TOKENS: response.usage.input_tokens
                    if response.usage
                    else None,
                    sc.GEN_AI_USAGE_OUTPUT_TOKENS: response.usage.output_tokens
                    if response.usage
                    else None,
                    sc.AS_USAGE_REPORTED: response.usage is not None,
                    sc.AS_MODEL_FAULT: response.injected_fault,
                }
            )
            llm.set_output({"content": response.content})
            llm.ok()
        if response.injected_fault:
            ctx.guardrail(
                "model_fault_injected",
                step + 1,
                f"model response corrupted by injected fault: {response.injected_fault}",
                span_id=llm.span_id,
            )

        try:
            decision = parse_decision(response.content)
        except DecisionError as exc:
            ctx.guardrail(
                "invalid_decision", step + 1, f"{exc.kind}: {exc}"[:300], span_id=llm.span_id
            )
            repairs = state.get("decision_repairs", 0)
            if repairs >= retry.max_decision_repairs:
                return {
                    "step": step + 1,
                    **end(
                        RunStatus.FAILED,
                        "invalid_decision",
                        "invalid decision; repair budget exhausted",
                        f"model response was not a valid decision ({exc.kind})",
                    ),
                }
            ctx.awaiting_decision_repair = True
            return {
                "step": step + 1,
                "decision_repairs": repairs + 1,
                "messages": [
                    ChatMessage(role="assistant", content=response.content[:4000]),
                    ChatMessage(
                        role="user",
                        content=(
                            f"Your previous response was not a valid decision ({exc}). "
                            f"{RESPONSE_FORMAT}"
                        ),
                        metadata={"kind": "feedback"},
                    ),
                ],
                "route": "plan",
                "reason": f"invalid decision ({exc.kind}); asked the model to correct it",
            }

        if ctx.awaiting_decision_repair:
            ctx.awaiting_decision_repair = False
            ctx.guardrail("decision_repaired", step + 1, "valid decision after corrective feedback")
        assistant = ChatMessage(
            role="assistant", content=response.content, metadata={"kind": "decision"}
        )
        if decision.extra_keys:
            span.set_attributes({sc.AS_DECISION_EXTRA_KEYS: ",".join(decision.extra_keys)[:500]})
        if isinstance(decision, ToolCallDecision):
            call_id = ctx.next_call_id()
            span.set_attributes({sc.AS_DECISION: "tool_call", sc.GEN_AI_TOOL_NAME: decision.tool})
            return {
                "step": step + 1,
                "messages": [assistant],
                "pending": {
                    "call_id": call_id,
                    "tool": decision.tool,
                    "arguments": decision.arguments,
                },
                "route": "guard",
                "reason": f"model requested {decision.tool}",
            }
        span.set_attributes({sc.AS_DECISION: "final"})
        if validated:
            return {
                "step": step + 1,
                "messages": [assistant],
                "candidate": decision.output,
                "route": "validate",
                "reason": "model produced a final answer",
            }
        return {
            "step": step + 1,
            "messages": [assistant],
            "candidate": decision.output,
            **end(RunStatus.COMPLETED, None, "model produced a final answer"),
        }

    async def guard(state: AgentState, span: SpanHandle) -> dict[str, Any]:
        call = ToolCall.model_validate(state.get("pending") or {})
        step = state.get("step", 0)

        def blocked(
            outcome: ToolOutcome, kind: GuardrailKind, reason: str, permitted: bool
        ) -> dict[str, Any]:
            with ctx.recorder.span(
                "guardrail tool_policy",
                SpanKind.GUARDRAIL,
                parent=span,
                attributes={sc.GEN_AI_TOOL_NAME: call.tool, sc.AS_GUARDRAIL_ALLOWED: False},
                input={"tool": call.tool, "arguments": call.arguments},
            ) as g:
                g.set_output({"allowed": False, "reason": reason})
                g.ok()
            ctx.guardrail(kind, step, reason, tool=call.tool, span_id=g.span_id)
            ctx.invocations.append(
                ToolInvocation(
                    call_id=call.call_id,
                    step=step,
                    tool=call.tool,
                    arguments=call.arguments,
                    permitted=permitted,
                    executed=False,
                    denial_reason=reason,
                    outcome=outcome,
                    span_id=g.span_id,
                )
            )
            result = ToolResult(
                call_id=call.call_id,
                tool=call.tool,
                outcome=outcome,
                error=f"blocked by the AgentScope runtime: {reason}",
                duration_ms=0.0,
            )
            return {"result": result.model_dump(mode="json"), "route": "observe", "reason": reason}

        # Existence first: a tool that does not exist (a hallucinated or corrupted name) is
        # unknown, not a denied attempt at something real, so it never counts as one.
        if ctx.executor.resolve(call.tool) is None:
            return blocked(
                ToolOutcome.UNKNOWN_TOOL, "unknown_tool", f"unknown tool '{call.tool}'", False
            )
        permission = check_permission(call.tool, ctx.permitted)
        if not permission.allowed:
            return blocked(ToolOutcome.PERMISSION_DENIED, "tool_denied", permission.reason, False)
        problem = ctx.executor.validate_arguments(call)
        if problem is not None:
            return blocked(
                ToolOutcome.INVALID_ARGUMENTS,
                "invalid_arguments",
                f"invalid arguments for {call.tool}: {problem}",
                True,
            )
        return {"route": "act", "reason": f"{call.tool} permitted; arguments valid"}

    async def act(state: AgentState, span: SpanHandle) -> dict[str, Any]:
        call = ToolCall.model_validate(state.get("pending") or {})
        step = state.get("step", 0)
        attempts: list[ToolAttempt] = []
        result: ToolResult | None = None

        def record(attempt: int, outcome: ToolResult, span_id: str) -> None:
            attempts.append(
                ToolAttempt(
                    attempt=attempt,
                    outcome=outcome.outcome,
                    fault=outcome.fault,
                    duration_ms=outcome.duration_ms,
                    error=outcome.error,
                    span_id=span_id,
                )
            )

        try:
            for attempt in range(1, retry.max_tool_retries + 2):
                started = time.perf_counter()
                with ctx.recorder.span(
                    f"execute_tool {call.tool}",
                    SpanKind.TOOL,
                    parent=span,
                    attributes={
                        sc.GEN_AI_OPERATION_NAME: "execute_tool",
                        sc.GEN_AI_TOOL_NAME: call.tool,
                        sc.GEN_AI_TOOL_CALL_ID: call.call_id,
                        sc.AS_ATTEMPT: attempt,
                    },
                    input=call.arguments,
                ) as tool_span:
                    try:
                        execution = await ctx.executor.run_attempt(call, attempt)
                    except asyncio.CancelledError:
                        # The run deadline cut this attempt short. The guard approved the
                        # call and it was running, so it stays on the record (the span is
                        # marked cancelled on the way out).
                        elapsed = (time.perf_counter() - started) * 1000
                        result = ctx.executor.interrupted(call, attempt, elapsed)
                        tool_span.set_attributes(
                            {
                                sc.AS_TOOL_OUTCOME: result.outcome.value,
                                sc.AS_FAULT_KIND: result.fault.value if result.fault else None,
                            }
                        )
                        record(attempt, result, tool_span.span_id)
                        raise
                    result = execution.result
                    tool_span.set_attributes(
                        {
                            sc.AS_TOOL_OUTCOME: result.outcome.value,
                            sc.AS_FAULT_KIND: result.fault.value if result.fault else None,
                            sc.AS_FAULT_UNDELIVERED: execution.undelivered.value
                            if execution.undelivered
                            else None,
                        }
                    )
                    tool_span.set_output(
                        {
                            "outcome": result.outcome.value,
                            "output": result.output,
                            "error": result.error,
                        }
                    )
                    if result.outcome is ToolOutcome.OK:
                        tool_span.ok()
                    else:
                        # Tool errors can echo arguments ("no project record with id ..."),
                        # so the exportable error type is the outcome alone.
                        tool_span.fail(
                            result.error or result.outcome.value, error_type=result.outcome.value
                        )
                record(attempt, result, tool_span.span_id)
                if result.outcome is ToolOutcome.OK or result.outcome not in retry.retry_on:
                    break
                if attempt <= retry.max_tool_retries:
                    span.add_event(
                        "tool_retry",
                        {
                            "tool": call.tool,
                            "after_outcome": result.outcome.value,
                            "retry.source": "runtime",
                        },
                    )
        finally:
            # Also on cancellation: checks must see an approved call even when the run
            # ended while it was executing.
            if attempts and result is not None:
                ctx.invocations.append(
                    ToolInvocation(
                        call_id=call.call_id,
                        step=step,
                        tool=call.tool,
                        arguments=call.arguments,
                        permitted=True,
                        executed=True,
                        outcome=result.outcome,
                        attempts=attempts,
                        output=result.output,
                        span_id=attempts[0].span_id,
                    )
                )
        assert result is not None
        suffix = f" after {len(attempts)} attempts" if len(attempts) > 1 else ""
        return {
            "result": result.model_dump(mode="json"),
            "route": "observe",
            "reason": f"{call.tool} returned {result.outcome.value}{suffix}",
        }

    async def observe(state: AgentState, span: SpanHandle) -> dict[str, Any]:
        result = ToolResult.model_validate(state.get("result") or {})
        pending = state.get("pending") or {}
        step = state.get("step", 0)
        isolate = ctx.config.isolate_untrusted
        if result.output is not None:
            findings = scan_untrusted(result.output)
            if findings:
                signatures = sorted({f.signature for f in findings})
                with ctx.recorder.span(
                    "guardrail content_scan",
                    SpanKind.GUARDRAIL,
                    parent=span,
                    attributes={
                        sc.GEN_AI_TOOL_NAME: result.tool,
                        sc.AS_GUARDRAIL_FINDINGS: len(findings),
                    },
                ) as g:
                    g.set_output(
                        {"signatures": signatures, "excerpts": [f.excerpt for f in findings]}
                    )
                    g.ok()
                ctx.guardrail(
                    "suspicious_content",
                    step,
                    f"instruction-like content in {result.tool} output: {', '.join(signatures)}",
                    tool=result.tool,
                    span_id=g.span_id,
                )
        message = ChatMessage(
            role="tool",
            name=result.tool,
            tool_call_id=result.call_id,
            content=render_observation(result, isolate=isolate),
            metadata={
                "kind": "observation",
                "call_id": result.call_id,
                "tool": result.tool,
                "arguments": pending.get("arguments", {}),
                "outcome": result.outcome.value,
                "output": result.output,
                "error": result.error,
                "delimited": isolate,
            },
        )
        span.set_attributes({sc.AS_OBSERVATION_DELIMITED: isolate})
        return {
            "messages": [message],
            "pending": None,
            "result": None,
            "route": "plan",
            "reason": f"{result.tool} result added to context"
            + (" as delimited untrusted data" if isolate else ""),
        }

    async def validate(state: AgentState, span: SpanHandle) -> dict[str, Any]:
        candidate = state.get("candidate")
        step = state.get("step", 0)
        repairs = state.get("output_repairs", 0)
        errors = output_errors(candidate, ctx.output_schema)
        span.set_attributes({sc.AS_OUTPUT_VALID: not errors})
        if not errors:
            if repairs:
                ctx.guardrail("output_repaired", step, f"output valid after {repairs} repair(s)")
            return end(RunStatus.COMPLETED, None, "final output matches the schema")
        detail = "; ".join(errors)
        ctx.guardrail("output_invalid", step, detail[:500], span_id=span.span_id)
        if repairs < retry.max_output_repairs:
            return {
                "output_repairs": repairs + 1,
                "candidate": None,
                "messages": [
                    ChatMessage(
                        role="user",
                        content=(
                            "Your final output does not match the required schema "
                            f"({detail}). Respond again with a corrected final answer."
                        ),
                        metadata={"kind": "feedback"},
                    )
                ],
                "route": "plan",
                "reason": "final output invalid; requested a repair",
            }
        return end(
            RunStatus.FAILED,
            "invalid_output",
            "final output invalid; repair budget exhausted",
            f"final output failed schema validation: {detail}"[:500],
        )

    async def finish(state: AgentState, span: SpanHandle) -> dict[str, Any]:
        span.set_attributes({sc.AS_RUN_STATUS: state.get("status") or "unknown"})
        return {"route": END, "reason": f"run ended: {state.get('status')}"}

    # Typed as Any: LangGraph's add_node overloads do not accept async closures cleanly.
    graph: Any = StateGraph(AgentState)
    graph.add_node("plan", node("plan", plan))
    graph.add_node("guard", node("guard", guard))
    graph.add_node("act", node("act", act))
    graph.add_node("observe", node("observe", observe))
    graph.add_node("finish", node("finish", finish))
    plan_targets = ["guard", "finish", "plan"]
    if validated:
        graph.add_node("validate", node("validate", validate))
        graph.add_conditional_edges("validate", route_of, ["finish", "plan"])
        plan_targets.append("validate")
    graph.add_edge(START, "plan")
    graph.add_conditional_edges("plan", route_of, plan_targets)
    graph.add_conditional_edges("guard", route_of, ["act", "observe"])
    graph.add_edge("act", "observe")
    graph.add_edge("observe", "plan")
    graph.add_edge("finish", END)
    return graph.compile()


def route_of(state: AgentState) -> str:
    return state["route"]


def recursion_limit(max_steps: int, max_decision_repairs: int, max_output_repairs: int) -> int:
    """Backstop only: the step budget in `plan` ends runs long before this is reached."""
    return 4 * max_steps + max_decision_repairs + 2 * max_output_repairs + 8


def topology(variant: GraphVariant) -> list[tuple[str, str]]:
    """The edge list for a graph variant (part of the effective config hash)."""
    edges = [
        ("START", "plan"),
        ("plan", "guard"),
        ("plan", "finish"),
        ("plan", "plan"),
        ("guard", "act"),
        ("guard", "observe"),
        ("act", "observe"),
        ("observe", "plan"),
        ("finish", "END"),
    ]
    if variant is GraphVariant.VALIDATED:
        edges += [("plan", "validate"), ("validate", "finish"), ("validate", "plan")]
    return sorted(edges)
