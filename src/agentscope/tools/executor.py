"""Executes one tool-call attempt: validation, fault injection, timeout, output checks."""

from __future__ import annotations

import asyncio
import json
import time
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
from agentscope.tools.faults import FaultPlan, payload_for
from agentscope.tools.registry import ToolRegistry


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
            return result(ToolOutcome.UNKNOWN_TOOL, error=f"unknown tool: {call.tool}")
        try:
            args = self._parse_arguments(tool, call)
        except ValidationError as exc:
            return result(ToolOutcome.INVALID_ARGUMENTS, error=summarize_validation_error(exc))

        fault = self._faults.next_call(call.tool)
        if fault and fault.kind is FaultKind.PERMISSION_DENIED:
            return result(
                ToolOutcome.PERMISSION_DENIED,
                error="simulated upstream refused access (HTTP 403)",
                fault=fault,
            )
        if fault and fault.kind is FaultKind.ERROR:
            return result(
                ToolOutcome.ERROR, error="simulated upstream error (HTTP 503)", fault=fault
            )

        try:
            async with asyncio.timeout(self._timeout_s):
                output = await self._run(tool, args, fault)
        except TimeoutError:
            return result(
                ToolOutcome.TIMEOUT,
                error=f"tool did not respond within {self._timeout_s:g}s",
                fault=fault if fault and fault.kind is FaultKind.TIMEOUT else None,
            )
        except ToolPermissionError as exc:
            return result(ToolOutcome.PERMISSION_DENIED, error=str(exc)[:500])
        except ToolExecutionError as exc:
            return result(ToolOutcome.ERROR, error=str(exc)[:500])
        except Exception as exc:  # a defect in a simulated tool; never leak a traceback
            return result(ToolOutcome.ERROR, error=f"internal tool error: {type(exc).__name__}")

        payload: Any = output.model_dump(mode="json")
        if fault is not None:
            payload = self._apply_output_fault(tool, payload, fault)

        try:
            validated = tool.output_model.model_validate(payload)
        except ValidationError as exc:
            preview = payload if isinstance(payload, str) else json.dumps(payload)[:2000]
            return result(
                ToolOutcome.MALFORMED,
                output=preview[:2000],
                error=f"tool returned a malformed response: {summarize_validation_error(exc)}",
                fault=fault,
            )

        clean: JsonValue = validated.model_dump(mode="json")
        if len(json.dumps(clean)) > self._max_output_chars:
            return result(
                ToolOutcome.ERROR,
                error=f"tool output exceeded {self._max_output_chars} characters",
                fault=fault,
            )
        return result(ToolOutcome.OK, output=clean, fault=fault)

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
