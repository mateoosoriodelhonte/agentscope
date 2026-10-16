"""create_task, send_email and delete_record only ever touch the per-run sandbox."""

from __future__ import annotations

from typing import Any

import pytest

from agentscope.agents.base import AgentCatalog
from agentscope.domain.tools import SideEffect, ToolCall, ToolOutcome, ToolResult
from agentscope.tools.base import ToolContext
from agentscope.tools.builtin.records import load_records
from agentscope.tools.executor import ToolExecutor
from agentscope.tools.registry import ToolRegistry

WRITE_TOOLS = ("create_task", "send_email", "delete_record")
FORBIDDEN_FOR_BUILTINS = ("send_email", "delete_record")


async def run(registry: ToolRegistry, ctx: ToolContext, tool: str, **arguments: Any) -> ToolResult:
    return await ToolExecutor(registry, ctx).execute(
        ToolCall(call_id="c1", tool=tool, arguments=arguments)
    )


async def test_create_task_appends_to_the_sandbox(registry: ToolRegistry) -> None:
    ctx = ToolContext(run_id="r")
    first = await run(
        registry,
        ctx,
        "create_task",
        title="Draft rollout notes",
        owner_id="emp-sol",
        estimate_hours=2,
    )
    second = await run(
        registry,
        ctx,
        "create_task",
        title="Review notes",
        owner_id="emp-ravi",
        estimate_hours=1.5,
        depends_on=["task-001"],
    )
    assert (first.outcome, second.outcome) == (ToolOutcome.OK, ToolOutcome.OK)
    assert [t["task_id"] for t in ctx.sandbox.tasks] == ["task-001", "task-002"]
    assert ctx.sandbox.tasks[1]["depends_on"] == ["task-001"]
    assert ToolContext(run_id="other").sandbox.tasks == []  # nothing is shared across runs


@pytest.mark.parametrize(
    ("arguments", "outcome"),
    [
        ({"title": "x", "owner_id": "emp-nobody", "estimate_hours": 1}, ToolOutcome.ERROR),
        ({"title": "", "owner_id": "emp-sol", "estimate_hours": 1}, ToolOutcome.INVALID_ARGUMENTS),
        ({"title": "x", "owner_id": "emp-sol", "estimate_hours": 0}, ToolOutcome.INVALID_ARGUMENTS),
        (
            {"title": "x" * 201, "owner_id": "emp-sol", "estimate_hours": 1},
            ToolOutcome.INVALID_ARGUMENTS,
        ),
        (
            {"title": "x", "owner_id": "emp-sol", "estimate_hours": "2"},
            ToolOutcome.INVALID_ARGUMENTS,
        ),
    ],
)
async def test_create_task_validation(
    registry: ToolRegistry, arguments: dict[str, Any], outcome: ToolOutcome
) -> None:
    ctx = ToolContext(run_id="r")
    assert (await run(registry, ctx, "create_task", **arguments)).outcome is outcome
    assert ctx.sandbox.tasks == []


async def test_send_email_only_fills_the_sandbox_outbox(registry: ToolRegistry) -> None:
    ctx = ToolContext(run_id="r")
    result = await run(
        registry, ctx, "send_email", to="audit@example.invalid", subject="Plan", body="Hello"
    )
    assert result.outcome is ToolOutcome.OK
    assert result.output["status"] == "stored_in_sandbox"  # type: ignore[index,call-overload]
    assert ctx.sandbox.outbox == [
        {"message_id": "msg-001", "to": "audit@example.invalid", "subject": "Plan", "body": "Hello"}
    ]


@pytest.mark.parametrize(
    "arguments",
    [
        {"to": "not-an-address", "subject": "s", "body": "b"},
        {"to": "a@example.invalid", "subject": "s", "body": "x" * 1025},
        {"to": "a@example.invalid", "body": "b"},
        {"to": "a@example.invalid", "subject": "s", "body": "b", "cc": "c@example.invalid"},
    ],
)
async def test_send_email_validation(registry: ToolRegistry, arguments: dict[str, Any]) -> None:
    ctx = ToolContext(run_id="r")
    result = await run(registry, ctx, "send_email", **arguments)
    assert result.outcome is ToolOutcome.INVALID_ARGUMENTS
    assert ctx.sandbox.outbox == []


async def test_delete_record_marks_the_sandbox_and_never_the_data(registry: ToolRegistry) -> None:
    ctx = ToolContext(run_id="r")
    deleted = await run(registry, ctx, "delete_record", record_id="prj-station-wifi")
    assert deleted.outcome is ToolOutcome.OK
    assert ctx.sandbox.deleted_records == ["prj-station-wifi"]
    again = await run(registry, ctx, "delete_record", record_id="prj-station-wifi")
    assert again.outcome is ToolOutcome.ERROR
    missing = await run(registry, ctx, "delete_record", record_id="prj-nothing")
    assert missing.outcome is ToolOutcome.ERROR
    assert "prj-station-wifi" in load_records().projects


def test_write_tools_declare_sandbox_side_effects(registry: ToolRegistry) -> None:
    for name in WRITE_TOOLS:
        tool = registry.resolve(name)
        assert tool is not None
        assert tool.spec().side_effect is SideEffect.SANDBOX_WRITE


def test_no_builtin_agent_version_permits_forbidden_tools(catalog: AgentCatalog) -> None:
    for definition in catalog.definitions():
        assert not set(FORBIDDEN_FOR_BUILTINS) & set(definition.toolset), definition.name
        for config in definition.versions:
            assert not set(FORBIDDEN_FOR_BUILTINS) & set(config.tools), config.ref
