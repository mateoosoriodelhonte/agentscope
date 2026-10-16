import pytest

from agentscope.domain.tools import FaultKind, FaultSpec, ToolCall, ToolOutcome
from agentscope.tools.base import ToolContext
from agentscope.tools.executor import ToolExecutor
from agentscope.tools.faults import DEFAULT_INJECTION, FaultPlan
from agentscope.tools.registry import ToolRegistry


def executor(
    registry: ToolRegistry, *faults: FaultSpec, version: int = 1, timeout: float = 0.05
) -> ToolExecutor:
    return ToolExecutor(
        registry,
        ToolContext(run_id="r"),
        fault_plan=FaultPlan(faults),
        schema_version=version,
        timeout_s=timeout,
    )


def call(tool: str, **arguments: object) -> ToolCall:
    return ToolCall(call_id="c1", tool=tool, arguments=arguments)  # type: ignore[arg-type]


async def test_search_returns_ranked_results(registry: ToolRegistry) -> None:
    result = await executor(registry).execute(
        call("search_documents", query="Meridian Footbridge opened")
    )
    assert result.outcome is ToolOutcome.OK
    assert isinstance(result.output, dict)
    ids = [hit["id"] for hit in result.output["results"]]
    assert "doc-meridian-footbridge" in ids


async def test_schema_v2_hides_unverified_sources_by_default(registry: ToolRegistry) -> None:
    v1 = await executor(registry, version=1).execute(
        call("search_documents", query="Meridian Footbridge trivia")
    )
    v2 = await executor(registry, version=2).execute(
        call("search_documents", query="Meridian Footbridge trivia")
    )
    assert any(h["reliability"] == "unverified" for h in v1.output["results"])  # type: ignore[index]
    assert all(h["reliability"] == "verified" for h in v2.output["results"])  # type: ignore[index]


@pytest.mark.parametrize(
    "arguments",
    [
        {"query": ""},
        {"query": "x", "top_k": 99},
        {"query": "x", "extra": 1},
        {"query": "x", "top_k": "3"},
    ],
)
async def test_invalid_arguments_are_rejected_strictly(
    registry: ToolRegistry, arguments: dict[str, object]
) -> None:
    result = await executor(registry).execute(call("search_documents", **arguments))
    assert result.outcome is ToolOutcome.INVALID_ARGUMENTS
    assert result.error


async def test_unknown_tool(registry: ToolRegistry) -> None:
    assert (await executor(registry).execute(call("rm_rf"))).outcome is ToolOutcome.UNKNOWN_TOOL


async def test_unknown_document_is_an_error_not_a_crash(registry: ToolRegistry) -> None:
    result = await executor(registry).execute(
        call("retrieve_document", doc_id="doc-does-not-exist")
    )
    assert result.outcome is ToolOutcome.ERROR


async def test_timeout_fault_is_enforced_by_a_real_deadline(registry: ToolRegistry) -> None:
    ex = executor(
        registry, FaultSpec(tool="search_documents", kind=FaultKind.TIMEOUT), timeout=0.05
    )
    result = await ex.execute(call("search_documents", query="ferry"))
    assert result.outcome is ToolOutcome.TIMEOUT
    assert result.fault is FaultKind.TIMEOUT
    assert 40 <= result.duration_ms < 2000


@pytest.mark.parametrize(
    ("kind", "outcome"),
    [
        (FaultKind.ERROR, ToolOutcome.ERROR),
        (FaultKind.PERMISSION_DENIED, ToolOutcome.PERMISSION_DENIED),
        (FaultKind.MALFORMED, ToolOutcome.MALFORMED),
        (FaultKind.INCOMPLETE, ToolOutcome.OK),
        (FaultKind.MISLEADING, ToolOutcome.OK),
        (FaultKind.INJECTION, ToolOutcome.OK),
    ],
)
async def test_every_fault_kind(
    registry: ToolRegistry, kind: FaultKind, outcome: ToolOutcome
) -> None:
    ex = executor(registry, FaultSpec(tool="retrieve_document", kind=kind))
    result = await ex.execute(call("retrieve_document", doc_id="doc-meridian-footbridge"))
    assert result.outcome is outcome
    assert result.fault is kind


async def test_content_faults_stay_schema_valid_but_change_content(registry: ToolRegistry) -> None:
    clean = await executor(registry).execute(
        call("retrieve_document", doc_id="doc-meridian-footbridge")
    )
    injected = await executor(
        registry, FaultSpec(tool="retrieve_document", kind=FaultKind.INJECTION)
    ).execute(call("retrieve_document", doc_id="doc-meridian-footbridge"))
    misleading = await executor(
        registry, FaultSpec(tool="retrieve_document", kind=FaultKind.MISLEADING)
    ).execute(call("retrieve_document", doc_id="doc-meridian-footbridge"))
    incomplete = await executor(
        registry, FaultSpec(tool="retrieve_document", kind=FaultKind.INCOMPLETE)
    ).execute(call("retrieve_document", doc_id="doc-meridian-footbridge"))
    assert injected.output["document"]["body"].endswith(DEFAULT_INJECTION)  # type: ignore[index]
    assert misleading.output != clean.output
    assert incomplete.output["document"]["facts"] == []  # type: ignore[index]
