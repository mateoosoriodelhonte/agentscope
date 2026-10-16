"""lookup_record, list_records and check_calendar over the packaged synthetic records."""

from __future__ import annotations

from typing import Any

import pytest

from agentscope.domain.tools import ToolCall, ToolOutcome, ToolResult
from agentscope.tools.base import ToolContext
from agentscope.tools.builtin.mock_api import load_mock_api
from agentscope.tools.builtin.records import MAX_WORKSTREAMS, load_calendar, load_records
from agentscope.tools.executor import ToolExecutor
from agentscope.tools.registry import ToolRegistry


async def run(
    registry: ToolRegistry, tool: str, ctx: ToolContext | None = None, **arguments: Any
) -> ToolResult:
    executor = ToolExecutor(registry, ctx or ToolContext(run_id="r"))
    return await executor.execute(ToolCall(call_id="c1", tool=tool, arguments=arguments))


def output(result: ToolResult) -> dict[str, Any]:
    assert result.outcome is ToolOutcome.OK, result.error
    assert isinstance(result.output, dict)
    return result.output


# --- lookup_record ------------------------------------------------------------------


async def test_lookup_project_lists_workstreams_and_notes(registry: ToolRegistry) -> None:
    record = output(
        await run(registry, "lookup_record", record_type="project", record_id="prj-fare-capping")
    )["record"]
    assert record["services"] == ["payments", "build-service"]
    assert [ws["estimate_hours"] for ws in record["workstreams"]] == [12, 30, 8, 16]
    assert record["workstreams"][1] == {
        "id": "ws-engine",
        "title": "Implement the capping engine",
        "owner_id": "emp-odile",
        "estimate_hours": 30.0,
        "depends_on": ["ws-api"],
    }
    assert record["workstream_count"] == 4
    assert record["notes"]


async def test_lookup_employee(registry: ToolRegistry) -> None:
    record = output(
        await run(registry, "lookup_record", record_type="employee", record_id="emp-mina")
    )["record"]
    assert (record["team"], record["role"], record["weekly_capacity_hours"]) == (
        "platform",
        "Site reliability engineer",
        36.0,
    )


@pytest.mark.parametrize(
    ("record_type", "record_id"),
    [("project", "prj-missing"), ("employee", "prj-fare-capping"), ("project", "emp-mina")],
)
async def test_lookup_is_an_exact_key_match_per_table(
    registry: ToolRegistry, record_type: str, record_id: str
) -> None:
    result = await run(registry, "lookup_record", record_type=record_type, record_id=record_id)
    assert result.outcome is ToolOutcome.ERROR
    assert result.error == f"no {record_type} record with id {record_id}"


@pytest.mark.parametrize(
    "arguments",
    [
        {"record_type": "project"},
        {"record_type": "vendor", "record_id": "prj-fare-capping"},
        {"record_type": "project", "record_id": "PRJ-FARE-CAPPING"},
        {"record_type": "project", "record_id": "../prj-fare-capping"},
        {"record_type": "project", "record_id": "x" * 65},
        {"record_type": "project", "record_id": "prj-fare-capping", "fields": ["notes"]},
        {"record_type": "project", "record_id": 7},
    ],
)
async def test_lookup_rejects_invalid_arguments(
    registry: ToolRegistry, arguments: dict[str, Any]
) -> None:
    result = await run(registry, "lookup_record", **arguments)
    assert result.outcome is ToolOutcome.INVALID_ARGUMENTS
    assert result.error


# --- list_records -------------------------------------------------------------------


async def test_list_projects_for_a_team(registry: ToolRegistry) -> None:
    listing = output(await run(registry, "list_records", record_type="project", team="payments"))
    assert listing["total"] == 2
    assert [(r["id"], r["status"]) for r in listing["records"]] == [
        ("prj-fare-capping", "active"),
        ("prj-ticket-api", "active"),
    ]


async def test_list_is_sorted_and_bounded(registry: ToolRegistry) -> None:
    listing = output(await run(registry, "list_records", record_type="employee", limit=3))
    assert listing["total"] == len(load_records().employees)
    assert [r["id"] for r in listing["records"]] == ["emp-ada", "emp-cato", "emp-ilse"]


async def test_unknown_team_lists_nothing(registry: ToolRegistry) -> None:
    listing = output(await run(registry, "list_records", record_type="project", team="finance"))
    assert (listing["total"], listing["records"]) == (0, [])


@pytest.mark.parametrize(
    "arguments",
    [
        {"record_type": "project", "limit": 0},
        {"record_type": "project", "limit": 21},
        {"record_type": "project", "team": "Payments Team"},
        {"record_type": "project", "limit": "5"},
    ],
)
async def test_list_rejects_invalid_arguments(
    registry: ToolRegistry, arguments: dict[str, Any]
) -> None:
    result = await run(registry, "list_records", **arguments)
    assert result.outcome is ToolOutcome.INVALID_ARGUMENTS


# --- check_calendar -----------------------------------------------------------------


async def test_calendar_subtracts_commitments_from_capacity(registry: ToolRegistry) -> None:
    week = output(await run(registry, "check_calendar", employee_id="emp-mina", week="2026-W45"))
    assert (week["capacity_hours"], week["booked_hours"], week["available_hours"]) == (36, 24, 12)
    assert [c["title"] for c in week["commitments"]] == ["Platform upgrade", "On-call rotation"]


async def test_free_covered_week_has_full_capacity(registry: ToolRegistry) -> None:
    week = output(await run(registry, "check_calendar", employee_id="emp-odile", week="2026-W44"))
    assert (week["available_hours"], week["commitments"]) == (32, [])


async def test_available_hours_never_go_negative(registry: ToolRegistry) -> None:
    week = output(await run(registry, "check_calendar", employee_id="emp-cato", week="2026-W45"))
    assert week["available_hours"] == 0


@pytest.mark.parametrize(
    ("employee_id", "week", "error"),
    [
        ("emp-nobody", "2026-W44", "no employee record with id emp-nobody"),
        ("prj-fare-capping", "2026-W44", "no employee record with id prj-fare-capping"),
        ("emp-mina", "2027-W01", "no calendar data for 2027-W01"),
    ],
)
async def test_calendar_errors(
    registry: ToolRegistry, employee_id: str, week: str, error: str
) -> None:
    result = await run(registry, "check_calendar", employee_id=employee_id, week=week)
    assert (result.outcome, result.error) == (ToolOutcome.ERROR, error)


@pytest.mark.parametrize(
    "week", ["2026-44", "2026-W54", "2026-W00", "2026-w44", "\uff12\uff10\uff12\uff16-W44"]
)
async def test_calendar_rejects_malformed_weeks(registry: ToolRegistry, week: str) -> None:
    result = await run(registry, "check_calendar", employee_id="emp-mina", week=week)
    assert result.outcome is ToolOutcome.INVALID_ARGUMENTS


# --- sandbox deletions and packaged data --------------------------------------------


async def test_records_deleted_in_the_run_sandbox_disappear_for_that_run(
    registry: ToolRegistry,
) -> None:
    ctx = ToolContext(run_id="r")
    ctx.sandbox.deleted_records.append("emp-ravi")
    lookup = await run(registry, "lookup_record", ctx, record_type="employee", record_id="emp-ravi")
    assert lookup.outcome is ToolOutcome.ERROR
    listing = output(
        await run(registry, "list_records", ctx, record_type="employee", team="payments")
    )
    assert [r["id"] for r in listing["records"]] == ["emp-odile"]
    fresh = await run(registry, "lookup_record", record_type="employee", record_id="emp-ravi")
    assert fresh.outcome is ToolOutcome.OK


def test_packaged_data_is_frozen() -> None:
    store = load_records()
    with pytest.raises(TypeError):
        store.projects["prj-new"] = store.projects["prj-fare-capping"]  # type: ignore[index]
    with pytest.raises(ValueError, match="frozen"):
        store.employees["emp-ada"].weekly_capacity_hours = 99  # type: ignore[misc]
    assert load_records() is store  # loaded once


def test_packaged_data_is_internally_consistent() -> None:
    store = load_records()
    calendar = load_calendar()
    status_paths = set(load_mock_api())
    for project in store.projects.values():
        assert project.workstream_count == len(project.workstreams) <= MAX_WORKSTREAMS
        seen: set[str] = set()
        for ws in project.workstreams:
            assert ws.owner_id in store.employees
            assert set(ws.depends_on) <= seen, f"{project.id}/{ws.id} depends on a later step"
            seen.add(ws.id)
        for service in project.services:
            assert f"/status/{service}" in status_paths
    assert set(calendar.commitments) <= set(store.employees)
    assert all(set(weeks) <= calendar.weeks for weeks in calendar.commitments.values())
