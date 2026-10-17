"""Simulated tool abstraction.

A tool is a pure-Python coroutine over packaged synthetic data. Tools never touch the
network, the host filesystem (beyond packaged read-only data), or a shell. Arguments and
outputs are validated against Pydantic models on every call.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict, JsonValue

from agentscope.domain.tools import SideEffect, ToolSpec


class ToolArgs(BaseModel):
    """Base for tool argument models: unknown arguments are rejected."""

    model_config = ConfigDict(extra="forbid")


class ToolOutput(BaseModel):
    """Base for tool output models."""

    model_config = ConfigDict(extra="forbid")


class ToolExecutionError(Exception):
    """A tool-level failure reported to the agent as an `error` outcome."""


class ToolPermissionError(Exception):
    """The simulated upstream system refused access (`permission_denied` outcome)."""


@dataclass
class Sandbox:
    """Per-run, in-memory state for simulated write tools. Discarded after the run."""

    tasks: list[dict[str, JsonValue]] = field(default_factory=list)
    outbox: list[dict[str, JsonValue]] = field(default_factory=list)
    deleted_records: list[str] = field(default_factory=list)


@dataclass
class ToolContext:
    run_id: str
    sandbox: Sandbox = field(default_factory=Sandbox)


class Tool(ABC):
    """A simulated tool. Subclasses declare models and implement `run`."""

    name: ClassVar[str]
    version: ClassVar[int] = 1
    description: ClassVar[str]
    args_model: ClassVar[type[ToolArgs]]
    output_model: ClassVar[type[ToolOutput]]
    side_effect: ClassVar[SideEffect] = SideEffect.NONE

    @abstractmethod
    async def run(self, args: Any, ctx: ToolContext) -> ToolOutput:
        """Execute with validated arguments and return a validated output model."""

    def spec(self) -> ToolSpec:
        schema = self.args_model.model_json_schema()
        schema.pop("title", None)
        return ToolSpec(
            name=self.name,
            version=self.version,
            description=self.description,
            parameters=schema,
            side_effect=self.side_effect,
        )

    # Fault hooks: how this tool's output degrades under injected faults. Each receives a
    # JSON-compatible copy of a valid output and must return a payload that still validates
    # against `output_model` (structurally valid, semantically wrong). Structural breakage is
    # the separate `malformed` fault. Returning the output unchanged means the fault had
    # nothing to act on (a search without hits): it is not delivered and stays owed.
    def make_incomplete(self, output: dict[str, Any]) -> dict[str, Any]:
        return _truncate_lists(output)

    def make_misleading(self, output: dict[str, Any], payload: str) -> dict[str, Any]:
        rewritten: dict[str, Any] = _rewrite_first_text(output, lambda _old: payload)
        return rewritten

    def inject_instructions(self, output: dict[str, Any], payload: str) -> dict[str, Any]:
        rewritten: dict[str, Any] = _rewrite_first_text(output, lambda old: f"{old}\n\n{payload}")
        return rewritten


def _truncate_lists(value: dict[str, Any]) -> dict[str, Any]:
    """Default 'incomplete' degradation: keep at most one element of every list."""
    out: dict[str, Any] = {}
    for key, item in value.items():
        if isinstance(item, list):
            out[key] = item[:1]
        elif isinstance(item, dict):
            out[key] = _truncate_lists(item)
        else:
            out[key] = item
    return out


def _rewrite_first_text(value: Any, rewrite: Callable[[str], str]) -> Any:
    """Apply `rewrite` to the first string leaf (depth-first) of a JSON value."""
    done = False

    def walk(node: Any) -> Any:
        nonlocal done
        if done:
            return node
        if isinstance(node, str):
            done = True
            return rewrite(node)
        if isinstance(node, list):
            return [walk(item) for item in node]
        if isinstance(node, dict):
            return {key: walk(item) for key, item in node.items()}
        return node

    return walk(value)
