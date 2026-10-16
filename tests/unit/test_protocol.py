import json

import pytest

from agentscope.runtime.protocol import (
    DecisionError,
    FinalDecision,
    ToolCallDecision,
    decision_schema,
    parse_decision,
)


def test_parses_tool_call_and_records_extra_keys() -> None:
    decision = parse_decision(
        json.dumps(
            {
                "type": "tool_call",
                "tool": "search_documents",
                "arguments": {"query": "x"},
                "rationale": "r",
                "confidence": 0.9,
            }
        )
    )
    assert isinstance(decision, ToolCallDecision)
    assert decision.tool == "search_documents"
    assert decision.extra_keys == ("confidence",)


def test_strips_think_blocks_fences_and_prose() -> None:
    raw = (
        "<think>let me {reason}</think>Sure!\n```json\n"
        '{"type": "final", "output": {"a": "b}"}, "rationale": ""}\n```'
    )
    decision = parse_decision(raw)
    assert isinstance(decision, FinalDecision)
    assert decision.output == {"a": "b}"}


@pytest.mark.parametrize(
    ("raw", "kind"),
    [
        ("", "empty"),
        ("   ", "empty"),
        ("no json here", "not_json"),
        ('{"type": "final", ', "not_json"),
        ('{"type": "dance"}', "schema"),
        ('{"type": "tool_call", "rationale": ""}', "schema"),
        ('{"type": "final", "rationale": ""}', "schema"),
        ('{"type": "tool_call", "tool": 5, "rationale": ""}', "schema"),
    ],
)
def test_invalid_decisions_are_classified(raw: str, kind: str) -> None:
    with pytest.raises(DecisionError) as info:
        parse_decision(raw)
    assert info.value.kind == kind


def test_error_messages_never_echo_values() -> None:
    with pytest.raises(DecisionError) as info:
        parse_decision(
            '{"type": "tool_call", "tool": "SECRET-VALUE-123", '
            '"arguments": "nope", "rationale": ""}'
        )
    assert "SECRET-VALUE-123" not in str(info.value)


def test_decision_schema_is_flat_and_inlines_refs() -> None:
    schema = decision_schema(
        {
            "type": "object",
            "$defs": {"X": {"type": "string"}},
            "properties": {"x": {"$ref": "#/$defs/X"}},
        }
    )
    assert schema["properties"]["tool"]["type"] == ["string", "null"]
    assert "$ref" not in json.dumps(schema)
