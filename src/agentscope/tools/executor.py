"""Executes one tool-call attempt: validation, fault injection, timeout, output checks."""

from __future__ import annotations

import asyncio
import copy
import json
import time
from dataclasses import dataclass
from typing import Any

from pydantic import JsonValue, ValidationError

from agentscope.domain.tools import FaultKind, FaultSpec, ToolCall, ToolOutcome, ToolResult
from agentscope.tools.base import (
    Tool,
    ToolContext,
    ToolExecutionError,
    ToolOutput,
    ToolPermissionError,
)
from agentscope.tools.faults import OUTPUT_FAULTS, FaultPlan, payload_for
from agentscope.tools.registry import ToolRegistry


@dataclass(frozen=True)
class Execution:
    result: ToolResult
    # An output fault was due on this attempt but had nothing to act on (the tool failed,
    # or applying it changed nothing). `result.fault` stays None and the fault stays owed.
    undelivered: FaultKind | None = None


def summarize_validation_error(error: ValidationError, limit: int = 3) -> str:
    """Compact, model-readable description of what was wrong with the arguments."""
    parts = []
    for item in error.errors()[:limit]:
        location = ".".join(str(p) for p in item["loc"]) or "(root)"
        parts.append(f"{location}: {item['msg']}")
    more = len(error.errors()) - limit
    if more > 0:
        parts.append(f"... and {more} more")
    return "; ".join(parts)


class ToolExecutor:
    def __init__(
        self,
        registry: ToolRegistry,
        ctx: ToolContext,
        *,
        fault_plan: FaultPlan | None = None,
        schema_version: int = 1,
        timeout_s: float = 2.0,
        max_output_chars: int = 20_000,
    ) -> None:
        self._registry = registry
        self._ctx = ctx
        self._faults = fault_plan or FaultPlan()
        self._schema_version = schema_version
        self._timeout_s = timeout_s
        self._max_output_chars = max_output_chars

    def resolve(self, name: str) -> Tool | None:
        return self._registry.resolve(name, self._schema_version)

    def validate_arguments(self, call: ToolCall) -> str | None:
        """Return an error description if the arguments are invalid, else None."""
        tool = self.resolve(call.tool)
        if tool is None:
            return f"unknown tool: {call.tool}"
        try:
            self._parse_arguments(tool, call)
        except ValidationError as exc:
            return summarize_validation_error(exc)
        return None

    @staticmethod
    def _parse_arguments(tool: Tool, call: ToolCall) -> Any:
        # Strict JSON-mode validation: no silent coercion ("5" is not 5, 1 is not True).
        return tool.args_model.model_validate_json(json.dumps(call.arguments), strict=True)

    async def execute(self, call: ToolCall, attempt: int = 1) -> ToolResult:
        return (await self.run_attempt(call, attempt)).result

    async def run_attempt(self, call: ToolCall, attempt: int = 1) -> Execution:
        """Execute one attempt; also reports an output fault that was due but not delivered."""
        started = time.perf_counter()

        def result(
            outcome: ToolOutcome,
            *,
            output: JsonValue | None = None,
            error: str | None = None,
            fault: FaultSpec | None = None,
        ) -> ToolResult:
            return ToolResult(
                call_id=call.call_id,
                tool=call.tool,
                attempt=attempt,
                outcome=outcome,
                output=output,
                error=error,
                duration_ms=(time.perf_counter() - started) * 1000,
                fault=fault.kind if fault else None,
            )

        tool = self.resolve(call.tool)
        if tool is None:
            return Execution(result(ToolOutcome.UNKNOWN_TOOL, error=f"unknown tool: {call.tool}"))
        try:
            args = self._parse_arguments(tool, call)
        except ValidationError as exc:
            return Execution(
                result(ToolOutcome.INVALID_ARGUMENTS, error=summarize_validation_error(exc))
            )

        fault = self._faults.next_call(call.tool)
        owed = fault if fault is not None and fault.kind in OUTPUT_FAULTS else None
        if fault and fault.kind is FaultKind.PERMISSION_DENIED:
            return Execution(
                result(
                    ToolOutcome.PERMISSION_DENIED,
                    error="simulated upstream refused access (HTTP 403)",
                    fault=fault,
                )
            )
        if fault and fault.kind is FaultKind.ERROR:
            return Execution(
                result(ToolOutcome.ERROR, error="simulated upstream error (HTTP 503)", fault=fault)
            )

        def failed(outcome: ToolOutcome, error: str, cause: FaultSpec | None = None) -> Execution:
            # The tool produced no output, so an output fault due on this call stays owed.
            return Execution(result(outcome, error=error, fault=cause), owed.kind if owed else None)

        try:
            async with asyncio.timeout(self._timeout_s):
                output = await self._run(tool, args, fault)
        except TimeoutError:
            return failed(
                ToolOutcome.TIMEOUT,
                f"tool did not respond within {self._timeout_s:g}s",
                fault if fault and fault.kind is FaultKind.TIMEOUT else None,
            )
        except ToolPermissionError as exc:
            return failed(ToolOutcome.PERMISSION_DENIED, str(exc)[:500])
        except ToolExecutionError as exc:
            return failed(ToolOutcome.ERROR, str(exc)[:500])
        except Exception as exc:  # a defect in a simulated tool; never leak a traceback
            return failed(ToolOutcome.ERROR, f"internal tool error: {type(exc).__name__}")

        payload: Any = output.model_dump(mode="json")
        applied: FaultSpec | None = None
        if owed is not None:
            # The hooks get a copy (as Tool documents), so a hook that edits its argument in
            # place cannot change the output while the comparison below sees no change.
            faulted = self._apply_output_fault(tool, copy.deepcopy(payload), owed)
            if faulted != payload:  # a fault that changes nothing was not delivered
                self._faults.delivered(owed)
                payload, applied = faulted, owed
        undelivered = owed.kind if owed is not None and applied is None else None

        try:
            validated = tool.output_model.model_validate(payload)
        except ValidationError as exc:
            preview = payload if isinstance(payload, str) else json.dumps(payload)[:2000]
            return Execution(
                result(
                    ToolOutcome.MALFORMED,
                    output=preview[:2000],
                    error=f"tool returned a malformed response: {summarize_validation_error(exc)}",
                    fault=applied,
                ),
                undelivered,
            )

        clean: JsonValue = validated.model_dump(mode="json")
        if len(json.dumps(clean)) > self._max_output_chars:
            return Execution(
                result(
                    ToolOutcome.ERROR,
                    error=f"tool output exceeded {self._max_output_chars} characters",
                    fault=applied,
                ),
                undelivered,
            )
        return Execution(result(ToolOutcome.OK, output=clean, fault=applied), undelivered)

    def interrupted(self, call: ToolCall, attempt: int, duration_ms: float) -> ToolResult:
        """The result of an attempt the run deadline cancelled mid-execution: a timeout,
        attributed to an injected hang when one was in effect. (Execution only awaits the
        tool itself, so the cancelled attempt is the latest call the fault plan counted; an
        output fault due on it was not delivered and stays owed.)"""
        fault = self._faults.last_due(call.tool)
        return ToolResult(
            call_id=call.call_id,
            tool=call.tool,
            attempt=attempt,
            outcome=ToolOutcome.TIMEOUT,
            error="cancelled: the run deadline passed while the tool was running",
            duration_ms=duration_ms,
            fault=FaultKind.TIMEOUT if fault and fault.kind is FaultKind.TIMEOUT else None,
        )

    async def _run(self, tool: Tool, args: Any, fault: FaultSpec | None) -> ToolOutput:
        if fault and fault.kind is FaultKind.TIMEOUT:
            await asyncio.Event().wait()  # a simulated hang, always cut short by the timeout
        return await tool.run(args, self._ctx)

    @staticmethod
    def _apply_output_fault(tool: Tool, payload: dict[str, Any], fault: FaultSpec) -> Any:
        match fault.kind:
            case FaultKind.MALFORMED:
                # Truncated JSON text where an object was expected.
                return json.dumps(payload)[: max(8, len(json.dumps(payload)) // 3)]
            case FaultKind.INCOMPLETE:
                return tool.make_incomplete(payload)
            case FaultKind.MISLEADING:
                return tool.make_misleading(payload, payload_for(fault))
            case FaultKind.INJECTION:
                return tool.inject_instructions(payload, payload_for(fault))
            case _:
                return payload
