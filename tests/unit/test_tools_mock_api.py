"""inspect_mock_api: allowlisted fixture paths only, never the network."""

from __future__ import annotations

import pytest

from agentscope.domain.tools import ToolCall, ToolOutcome, ToolResult
from agentscope.tools.base import ToolContext
from agentscope.tools.builtin.mock_api import InspectMockApi, load_mock_api
from agentscope.tools.executor import ToolExecutor
from agentscope.tools.registry import ToolRegistry


async def get(registry: ToolRegistry, path: object) -> ToolResult:
    executor = ToolExecutor(registry, ToolContext(run_id="r"))
    return await executor.execute(
        ToolCall(call_id="c1", tool="inspect_mock_api", arguments={"path": path})  # type: ignore[dict-item]
    )


async def test_status_fixture(registry: ToolRegistry) -> None:
    result = await get(registry, "/status/payments")
    assert result.outcome is ToolOutcome.OK
    assert result.output == {
        "path": "/status/payments",
        "status_code": 200,
        "body": {
            "service": "payments",
            "state": "degraded",
            "source": "live",
            "as_of": "2026-10-15",
            "message": "Settlement batches are delayed by up to two hours.",
        },
    }


@pytest.mark.parametrize(
    "path", ["/status//payments", "/status/./payments", "///status/payments", "/status/payments/"]
)
async def test_paths_are_normalized_before_the_exact_match(
    registry: ToolRegistry, path: str
) -> None:
    result = await get(registry, path)
    assert result.outcome is ToolOutcome.OK
    assert result.output["path"] == "/status/payments"  # type: ignore[index,call-overload]


@pytest.mark.parametrize(
    "path",
    [
        "status/payments",
        "/status/../v1/releases/latest",
        "/status/%2e%2e/secrets",
        "/status\\payments",
        "/redirect?to=http://example.invalid",
        "http://example.invalid/status/payments",
        "/status/pay ments",
        "/status/paymënts",
        "/" + "a" * 200,
        "",
        42,
    ],
)
async def test_unsafe_paths_are_invalid_arguments(registry: ToolRegistry, path: object) -> None:
    result = await get(registry, path)
    assert result.outcome is ToolOutcome.INVALID_ARGUMENTS
    assert result.error


@pytest.mark.parametrize("path", ["/status/unknown", "/", "/v1/releases", "//status/payments"])
async def test_unlisted_paths_are_errors(registry: ToolRegistry, path: str) -> None:
    result = await get(registry, path)
    assert result.outcome is ToolOutcome.ERROR
    assert result.error is not None
    assert result.error.startswith("no route for GET ")


def test_fixtures_are_read_only() -> None:
    fixtures = load_mock_api()
    with pytest.raises(TypeError):
        fixtures["/status/payments"].body["state"] = "operational"  # type: ignore[index]
    with pytest.raises(TypeError):
        fixtures["/status/new"] = fixtures["/status/payments"]  # type: ignore[index]
    assert fixtures["/status/payments"].body["state"] == "degraded"
    assert load_mock_api() is fixtures  # loaded once


def test_description_names_every_fixture() -> None:
    for path in load_mock_api():
        name = path.removeprefix("/status/")
        assert name in InspectMockApi.description, path
