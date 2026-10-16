"""Built-in simulated tools."""

from __future__ import annotations

from agentscope.tools.base import Tool
from agentscope.tools.builtin.calculator import Calculate
from agentscope.tools.builtin.documents import (
    RetrieveDocument,
    SearchDocumentsV1,
    SearchDocumentsV2,
)
from agentscope.tools.builtin.mock_api import InspectMockApi
from agentscope.tools.builtin.records import CheckCalendar, ListRecords, LookupRecord
from agentscope.tools.builtin.sandbox import CreateTask, DeleteRecord, SendEmail


def builtin_tools() -> list[Tool]:
    return [
        SearchDocumentsV1(),
        SearchDocumentsV2(),
        RetrieveDocument(),
        LookupRecord(),
        ListRecords(),
        CheckCalendar(),
        Calculate(),
        InspectMockApi(),
        CreateTask(),
        SendEmail(),
        DeleteRecord(),
    ]
