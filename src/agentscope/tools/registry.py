"""Registry of simulated tools, with versioned schemas."""

from __future__ import annotations

from collections.abc import Iterable

from agentscope.domain.tools import ToolSpec
from agentscope.tools.base import Tool


class ToolRegistry:
    """Maps tool names to their schema versions.

    An agent's `tool_schema_version` selects, per tool, the highest registered version
    that is less than or equal to it, so schema revisions can be compared side by side.
    """

    def __init__(self, tools: Iterable[Tool]) -> None:
        self._tools: dict[str, dict[int, Tool]] = {}
        for tool in tools:
            versions = self._tools.setdefault(tool.name, {})
            if tool.version in versions:
                raise ValueError(f"duplicate tool registration: {tool.name} v{tool.version}")
            versions[tool.version] = tool

    def names(self) -> list[str]:
        return sorted(self._tools)

    def versions(self, name: str) -> list[int]:
        return sorted(self._tools.get(name, {}))

    def resolve(self, name: str, schema_version: int = 1) -> Tool | None:
        versions = self._tools.get(name)
        if not versions:
            return None
        eligible = [v for v in versions if v <= schema_version]
        return versions[max(eligible)] if eligible else versions[min(versions)]

    def specs(self, names: Iterable[str], schema_version: int = 1) -> list[ToolSpec]:
        specs = []
        for name in names:
            tool = self.resolve(name, schema_version)
            if tool is not None:
                specs.append(tool.spec())
        return specs

    def all_specs(self) -> list[ToolSpec]:
        return [tool.spec() for versions in self._tools.values() for tool in versions.values()]


def default_registry() -> ToolRegistry:
    from agentscope.tools.builtin import builtin_tools

    return ToolRegistry(builtin_tools())
