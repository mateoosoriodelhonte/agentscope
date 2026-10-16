"""Built-in simulated tools."""

from __future__ import annotations

from agentscope.tools.base import Tool
from agentscope.tools.builtin.documents import (
    RetrieveDocument,
    SearchDocumentsV1,
    SearchDocumentsV2,
)


def builtin_tools() -> list[Tool]:
    return [SearchDocumentsV1(), SearchDocumentsV2(), RetrieveDocument()]
