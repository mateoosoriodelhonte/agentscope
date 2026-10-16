"""calculate: safe arithmetic, with every rejection surfacing as invalid_arguments."""

from __future__ import annotations

import pytest

from agentscope.domain.tools import ToolCall, ToolOutcome
from agentscope.tools.base import ToolContext
from agentscope.tools.builtin.calculator import CalculationError, evaluate
from agentscope.tools.executor import ToolExecutor
from agentscope.tools.registry import ToolRegistry


@pytest.mark.parametrize(
    ("expression", "expected"),
    [
        ("12 + 30 + 8 + 16", 66),
        ("10 + 18.5 + 4", 32.5),
        ("2 + 3 * 4", 14),
        ("(2 + 3) * 4", 20),
        ("7 / 2", 3.5),
        ("7 // 2", 3),
        ("-7 % 3", 2),
        ("-2 ** 2", -4),
        ("(-2) ** 2", 4),
        ("2 ** -1", 0.5),
        ("+5 - -5", 10),
        ("1e3 * 2", 2000),
        ("0.1 + 0.2", 0.1 + 0.2),
        ("1000000 ** 2 * 1000", 1e15),
    ],
)
def test_arithmetic_matches_python(expression: str, expected: float) -> None:
    assert evaluate(expression) == pytest.approx(expected)


@pytest.mark.parametrize(
    ("expression", "reason"),
    [
        ("", "not a valid arithmetic expression"),
        ("2 +", "not a valid arithmetic expression"),
        ("x + 1", "Name is not allowed"),
        ("__import__('os').system('true')", "Call is not allowed"),
        ("(1).real", "Attribute is not allowed"),
        ("[1, 2][0]", "Subscript is not allowed"),
        ("'a' * 3", "only int and float literals are allowed"),
        ("True + 1", "only int and float literals are allowed"),
        ("1j * 2", "only int and float literals are allowed"),
        ("1 < 2", "Compare is not allowed"),
        ("~1", "Invert is not allowed"),
        ("lambda: 1", "Lambda is not allowed"),
        ("(1)(2)", "Call is not allowed"),
        ("1 / 0", "division by zero"),
        ("5 // 0.0", "division by zero"),
        ("5 % 0", "division by zero"),
        ("0 ** -1", "division by zero"),
        ("1e999", "exceeds"),
        ("1e-300 ** -10", "out of range"),
        ("1e16", "exceeds"),
        ("10 ** 8 * 10 ** 8", "exceeds"),
        ("2 ** 11", "exponent"),
        ("1000001 ** 2", "base"),
        ("(-8) ** 0.5", "not a real number"),
        ("1" + " + 1" * 30, "syntax nodes"),
        ("((((((((((1))))))))))", None),
        ("-" * 10 + "1", "nested deeper"),
        ("1" * 201, "longer than 200"),
        ("1\x00", "not a valid arithmetic expression"),
    ],
)
def test_rejections(expression: str, reason: str | None) -> None:
    if reason is None:  # redundant parentheses add no nodes, so this one is fine
        assert evaluate(expression) == 1
        return
    with pytest.raises(CalculationError, match=reason):
        evaluate(expression)


def test_sum_of_nine_terms_fits_the_depth_bound() -> None:
    assert evaluate(" + ".join(["1"] * 9)) == 9
    with pytest.raises(CalculationError, match="nested deeper"):
        evaluate(" + ".join(["1"] * 10))


def executor(registry: ToolRegistry) -> ToolExecutor:
    return ToolExecutor(registry, ToolContext(run_id="r"))


async def test_tool_returns_result_and_display(registry: ToolRegistry) -> None:
    result = await executor(registry).execute(
        ToolCall(call_id="c1", tool="calculate", arguments={"expression": "10 + 18.5 + 4"})
    )
    assert result.outcome is ToolOutcome.OK
    assert result.output == {"expression": "10 + 18.5 + 4", "result": 32.5, "display": "32.5"}


@pytest.mark.parametrize(
    "arguments",
    [
        {"expression": "1 / 0"},
        {"expression": "open('/etc/passwd').read()"},
        {"expression": 5},
        {"expression": "1 + 1", "precision": 2},
    ],
)
async def test_rejected_expressions_are_invalid_arguments(
    registry: ToolRegistry, arguments: dict[str, object]
) -> None:
    ex = executor(registry)
    call = ToolCall(call_id="c1", tool="calculate", arguments=arguments)  # type: ignore[arg-type]
    assert ex.validate_arguments(call) is not None  # the runtime guard blocks it before `act`
    result = await ex.execute(call)
    assert result.outcome is ToolOutcome.INVALID_ARGUMENTS
    assert result.error
