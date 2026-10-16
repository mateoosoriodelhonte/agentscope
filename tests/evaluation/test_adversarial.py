"""Anti-gaming: scripted brains that try to pass without doing the task must fail.

- spray: calls every permitted tool, hoping required calls get covered by volume;
- hedge: answers with every candidate value it saw, hoping one of them matches;
- abstain: always reports insufficient evidence, hoping caution is never penalised.

The honest research brain (same configuration) passes every case, so each failure is the
adversary's, not the dataset's.
"""

from __future__ import annotations

from typing import Any

import pytest

from agentscope.agents.base import AgentCatalog
from agentscope.agents.research import ResearchBrain
from agentscope.domain.datasets import Dataset
from agentscope.domain.evaluation import CheckName, CheckStatus, SuiteResult
from agentscope.providers.fake import Brain, ConversationView, FakeModelProvider, final, tool_call
from agentscope.runtime.runner import AgentRunner
from agentscope.suite_runner import SuiteRunner
from agentscope.tools.registry import ToolRegistry

LIMITS = {"tool_timeout_s": 0.05}
DATASET = Dataset.model_validate(
    {
        "name": "anti-gaming",
        "cases": [
            {
                "id": "bridge-year",
                "task": "In what year did the Meridian Footbridge open?",
                "expected": {"match": "contains", "value": "1987", "max_chars": 20},
                "expected_tool_calls": [
                    {"tool": "search_documents"},
                    {"tool": "retrieve_document", "max_calls": 2},
                ],
                "limits": LIMITS,
            },
            {
                "id": "ferry-minutes",
                "task": "How long does the Glasswing Ferry crossing take in minutes?",
                "expected": {"match": "numeric", "value": 25},
                "tool_order": ["search_documents", "retrieve_document"],
                "tool_order_strict": True,
                "limits": LIMITS,
            },
            {
                "id": "observatory-outage",
                "task": "How many visitors did Kestrel Hill Observatory have in 2025?",
                "faults": [{"tool": "search_documents", "kind": "error", "on_call": None}],
                "success_criteria": {
                    "expect_recovery": False,
                    "expected_output_status": "insufficient_evidence",
                },
                "limits": LIMITS,
            },
        ],
    }
)


class Spray:
    """Calls each permitted tool once before letting the honest brain finish."""

    def decide(self, view: ConversationView) -> dict[str, Any]:
        called = {o.tool for o in view.observations}
        arguments = {
            "search_documents": {"query": view.task[:200], "top_k": 5},
            "retrieve_document": {"doc_id": "doc-central-library"},
        }
        for spec in view.tools:
            if spec.name not in called:
                return tool_call(spec.name, arguments[spec.name], "Call everything.")
        return ResearchBrain().decide(view)


class Hedge:
    """Researches honestly, then answers with every fact value it read."""

    def decide(self, view: ConversationView) -> dict[str, Any]:
        decision = ResearchBrain().decide(view)
        output = decision.get("output")
        if not isinstance(output, dict) or output.get("status") != "answered":
            return decision
        values = [
            str(fact["value"])
            for obs in view.calls_to("retrieve_document")
            if obs.ok and isinstance(obs.output, dict)
            for fact in obs.output["document"]["facts"]
        ]
        return final({**output, "answer": ", ".join(dict.fromkeys(values))[:500]}, "Hedge.")


class Abstain:
    def decide(self, view: ConversationView) -> dict[str, Any]:
        return final(
            {
                "status": "insufficient_evidence",
                "answer": None,
                "citations": [],
                "confidence": "low",
            },
            "Never commit.",
        )


async def _run(brain: Brain, catalog: AgentCatalog, registry: ToolRegistry) -> SuiteResult:
    runner = AgentRunner(
        catalog, registry, {"fake": FakeModelProvider({"research_assistant": brain})}
    )
    return await SuiteRunner(runner, catalog).run(DATASET, catalog.resolve("research_assistant@v3"))


def _failures(result: SuiteResult) -> dict[str, set[CheckName]]:
    return {
        case.case_id: {c.name for c in case.checks if c.status is CheckStatus.FAILED}
        for case in result.cases
        if not case.passed
    }


async def test_honest_brain_passes_every_case(
    catalog: AgentCatalog, registry: ToolRegistry
) -> None:
    result = await _run(ResearchBrain(), catalog, registry)
    assert _failures(result) == {}


@pytest.mark.parametrize(
    ("brain", "case_id", "caught_by"),
    [
        (Spray(), "bridge-year", CheckName.REQUIRED_TOOL_CALLS),
        (Hedge(), "bridge-year", CheckName.ANSWER_CORRECTNESS),
        (Hedge(), "ferry-minutes", CheckName.ANSWER_CORRECTNESS),
        (Abstain(), "bridge-year", CheckName.ANSWER_CORRECTNESS),
        (Abstain(), "ferry-minutes", CheckName.TOOL_ORDER),
    ],
    ids=[
        "spray-volume",
        "hedge-long",
        "hedge-list",
        "abstain",
        "abstain-order",
    ],
)
async def test_gaming_strategies_fail(
    brain: Brain,
    case_id: str,
    caught_by: CheckName,
    catalog: AgentCatalog,
    registry: ToolRegistry,
) -> None:
    failures = _failures(await _run(brain, catalog, registry))
    assert caught_by in failures.get(case_id, set()), failures
