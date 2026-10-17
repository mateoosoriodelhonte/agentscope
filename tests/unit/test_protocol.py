import json
import time

import pytest

from agentscope.runtime.protocol import (
    MAX_DECISION_DEPTH,
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


def _final(output: dict[str, object]) -> str:
    return json.dumps({"type": "final", "output": output, "rationale": ""})


@pytest.mark.parametrize(
    "answer",
    [
        "Run:\n```bash\npip install x\n```",
        "```json\n{}\n```",
        "Wrap reasoning in <think>tags</think> only when asked.",
        "<THINK>unclosed",
    ],
)
def test_fences_and_think_tags_inside_json_strings_are_kept(answer: str) -> None:
    decision = parse_decision(_final({"answer": answer}))
    assert isinstance(decision, FinalDecision)
    assert decision.output == {"answer": answer}


def test_a_leading_object_wins_over_a_later_fenced_block() -> None:
    raw = _final({"answer": "first"}) + "\n```json\n" + _final({"answer": "second"}) + "\n```"
    decision = parse_decision(raw)
    assert isinstance(decision, FinalDecision)
    assert decision.output == {"answer": "first"}


def test_prose_braces_before_a_fenced_decision_are_skipped() -> None:
    raw = "Here is my {decision}:\n```json\n" + _final({"answer": "ok"}) + "\n```"
    decision = parse_decision(raw)
    assert isinstance(decision, FinalDecision)
    assert decision.output == {"answer": "ok"}


def test_think_blocks_before_the_decision_are_skipped_even_with_json_inside() -> None:
    raw = '<think>maybe {"type": "tool_call", "tool": "x"}?</think>\n' + _final({"answer": "ok"})
    decision = parse_decision(raw)
    assert isinstance(decision, FinalDecision)
    assert decision.output == {"answer": "ok"}


@pytest.mark.parametrize(
    "raw",
    [
        "<think>" * 14_000,  # unclosed think tags: each one used to rescan to the end
        "<think>" + "{" * 90_000,
        "{}" * 45_000 + "<think>",
        "<think></think>" * 6_000 + "x" * 9_000 + "{",
    ],
    ids=["unclosed-think", "think-then-braces", "empty-objects", "closed-thinks"],
)
def test_adversarial_responses_are_rejected_in_linear_time(raw: str) -> None:
    started = time.perf_counter()
    with pytest.raises(DecisionError):
        parse_decision(raw)
    assert time.perf_counter() - started < 1.0


def test_deeply_nested_responses_are_invalid_decisions_not_crashes() -> None:
    nested = "[" * 49_000 + "]" * 49_000
    raw = '{"type": "final", "rationale": "", "output": {"a": ' + nested + "}}"
    with pytest.raises(DecisionError) as info:
        parse_decision(raw)
    assert info.value.kind == "too_large"


def test_moderate_nesting_is_still_accepted() -> None:
    output: dict[str, object] = {"leaf": "x"}
    for _ in range(MAX_DECISION_DEPTH - 3):
        output = {"n": output}
    decision = parse_decision(_final(output))
    assert isinstance(decision, FinalDecision)


def test_numbers_python_refuses_to_convert_are_invalid_decisions() -> None:
    raw = '{"type": "final", "rationale": "", "output": {"n": 1' + "0" * 5000 + "}}"
    with pytest.raises(DecisionError) as info:
        parse_decision(raw)
    assert info.value.kind == "not_json"


def test_nul_and_lone_surrogates_are_replaced_so_traces_stay_storable() -> None:
    raw = (
        '{"type": "final", "rationale": "", '
        '"output": {"answer": "a\\u0000b\\ud83dc \\ud83d\\ude00", "k\\udc00": 1}}'
    )
    decision = parse_decision(raw)
    assert isinstance(decision, FinalDecision)
    assert decision.output == {"answer": "a\ufffdb\ufffdc \U0001f600", "k\ufffd": 1}
    json.dumps(decision.output, ensure_ascii=False).encode("utf-8")  # strict UTF-8 works


def test_tool_names_are_sanitized_too() -> None:
    raw = '{"type": "tool_call", "tool": "search\\u0000", "arguments": {}, "rationale": ""}'
    decision = parse_decision(raw)
    assert isinstance(decision, ToolCallDecision)
    assert decision.tool == "search\ufffd"
