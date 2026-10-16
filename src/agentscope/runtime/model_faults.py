"""Deterministic corruption of model responses, to exercise decision/output repair paths."""

from __future__ import annotations

import json
from collections.abc import Sequence

from agentscope.domain.tools import ModelFaultKind, ModelFaultSpec
from agentscope.providers.base import ModelProvider, ModelRequest, ModelResponse


class FaultyModelProvider:
    """Wraps a provider and corrupts the n-th responses named by the fault specs."""

    def __init__(self, inner: ModelProvider, faults: Sequence[ModelFaultSpec]) -> None:
        self._inner = inner
        self._faults = list(faults)
        self._calls = 0
        self.name = inner.name

    def _fault_for(self, n: int) -> ModelFaultSpec | None:
        for fault in self._faults:
            if fault.on_call <= n < fault.on_call + fault.times:
                return fault
        return None

    async def generate(self, request: ModelRequest) -> ModelResponse:
        self._calls += 1
        response = await self._inner.generate(request)
        fault = self._fault_for(self._calls)
        if fault is None:
            return response
        return response.model_copy(
            update={
                "content": corrupt(response.content, fault.kind),
                "injected_fault": fault.kind.value,
            }
        )


def corrupt(content: str, kind: ModelFaultKind) -> str:
    match kind:
        case ModelFaultKind.EMPTY:
            return ""
        case ModelFaultKind.INVALID_JSON:
            return content[: max(1, len(content) // 2)] + " <<truncated"
        case ModelFaultKind.SCHEMA_VIOLATION:
            return json.dumps(
                {"type": "final", "output": {"unexpected_field": True}, "rationale": "corrupted"}
            )
        case ModelFaultKind.UNKNOWN_TOOL:
            return json.dumps(
                {
                    "type": "tool_call",
                    "tool": "nonexistent_tool",
                    "arguments": {},
                    "rationale": "corrupted",
                }
            )
