"""Safe arithmetic for the `calculate` tool.

The expression is parsed with `ast` and evaluated by a small interpreter over an allowlist
of node types (Expression, BinOp with + - * / // % **, UnaryOp with + -, and int or float
Constants). Nothing is ever handed to Python's own evaluator, and names, calls, attributes,
and subscripts cannot appear because their node types are not on the list.

Bounds: at most 200 characters, 50 AST nodes (everything `ast.walk` yields, operators
included) and a nesting depth of 10 counted from the Expression root; `**` only when
|base| <= 1e6 and |exponent| <= 10; every literal and intermediate value at most 1e15 in
magnitude. Arithmetic runs in float.

Every rejection (syntax, a disallowed node, a bound, division by zero, a complex or
non-finite result) is raised while validating the arguments, so it surfaces as
`invalid_arguments` and the runtime guard blocks the call before it executes. The tool
itself never fails.
"""

from __future__ import annotations

import ast
import math
import operator
import warnings
from collections.abc import Callable
from typing import Any, ClassVar

from pydantic import Field, field_validator

from agentscope.tools.base import Tool, ToolArgs, ToolContext, ToolOutput
from agentscope.tools.builtin.common import Text, append_text

MAX_EXPRESSION_CHARS = 200
MAX_NODES = 50
MAX_DEPTH = 10
MAX_MAGNITUDE = 1e15
MAX_POWER_BASE = 1e6
MAX_POWER_EXPONENT = 10

_BINARY: dict[type[ast.operator], Callable[[float, float], Any]] = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
_UNARY: dict[type[ast.unaryop], Callable[[float], float]] = {
    ast.UAdd: operator.pos,
    ast.USub: operator.neg,
}
_ALLOWED_NODES = (ast.Expression, ast.BinOp, ast.UnaryOp, ast.Constant, *_BINARY, *_UNARY)


class CalculationError(ValueError):
    """The expression is outside the allowed grammar or its bounds."""


def evaluate(expression: str) -> float:
    if len(expression) > MAX_EXPRESSION_CHARS:
        raise CalculationError(f"expression longer than {MAX_EXPRESSION_CHARS} characters")
    try:
        # Parsing only builds a tree; warnings (e.g. "'int' object is not callable") would
        # just be noise for input that is rejected below anyway.
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            tree = ast.parse(expression, mode="eval")
    except (SyntaxError, ValueError, RecursionError):
        raise CalculationError("not a valid arithmetic expression") from None
    nodes = list(ast.walk(tree))
    if len(nodes) > MAX_NODES:
        raise CalculationError(f"expression has more than {MAX_NODES} syntax nodes")
    for node in nodes:
        if not isinstance(node, _ALLOWED_NODES):
            raise CalculationError(f"{type(node).__name__} is not allowed")
    if _depth(tree) > MAX_DEPTH:
        raise CalculationError(f"expression is nested deeper than {MAX_DEPTH} levels")
    return _evaluate(tree.body)


def _depth(node: ast.AST) -> int:
    # Only reached with at most MAX_NODES nodes, so the recursion is shallow.
    return 1 + max((_depth(child) for child in ast.iter_child_nodes(node)), default=0)


def _evaluate(node: ast.expr) -> float:
    if isinstance(node, ast.Constant):
        if isinstance(node.value, bool) or not isinstance(node.value, int | float):
            raise CalculationError("only int and float literals are allowed")
        return _bounded(node.value)
    if isinstance(node, ast.UnaryOp):
        return _bounded(_UNARY[type(node.op)](_evaluate(node.operand)))
    assert isinstance(node, ast.BinOp)  # evaluate() rejected every other node type
    left, right = _evaluate(node.left), _evaluate(node.right)
    if isinstance(node.op, ast.Pow) and (
        abs(left) > MAX_POWER_BASE or abs(right) > MAX_POWER_EXPONENT
    ):
        raise CalculationError(
            f"** needs |base| <= {MAX_POWER_BASE:g} and |exponent| <= {MAX_POWER_EXPONENT}"
        )
    try:
        return _bounded(_BINARY[type(node.op)](left, right))
    except ZeroDivisionError:
        raise CalculationError("division by zero") from None
    except OverflowError:
        raise CalculationError("result out of range") from None


def _bounded(value: object) -> float:
    # A negative base with a fractional exponent yields a complex number.
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise CalculationError("result is not a real number")
    # Compared before converting, so huge int literals never overflow float(). Written so
    # that infinities and NaN (every comparison with NaN is false) are rejected too.
    if not abs(value) <= MAX_MAGNITUDE:
        raise CalculationError(f"value is not finite or exceeds {MAX_MAGNITUDE:g} in magnitude")
    return float(value)


def format_result(value: float) -> str:
    return f"{value:.12g}"


class CalculateArgs(ToolArgs):
    expression: str = Field(
        min_length=1,
        max_length=MAX_EXPRESSION_CHARS,
        description="Arithmetic over number literals, e.g. '12 + 30 + 8.5'.",
    )

    @field_validator("expression")
    @classmethod
    def _evaluable(cls, value: str) -> str:
        evaluate(value)  # CalculationError is a ValueError: reported as invalid_arguments
        return value


class CalculateOutput(ToolOutput):
    expression: str = Field(max_length=MAX_EXPRESSION_CHARS)
    result: float = Field(allow_inf_nan=False)
    display: Text


class Calculate(Tool):
    name = "calculate"
    description = (
        "Evaluate arithmetic over int and float literals with + - * / // % ** and "
        "parentheses, e.g. '12 + 30 + 8'. No names, functions, or variables; at most "
        "200 characters. Returns the result as a number and as display text."
    )
    args_model: ClassVar[type[ToolArgs]] = CalculateArgs
    output_model = CalculateOutput

    async def run(self, args: CalculateArgs, ctx: ToolContext) -> CalculateOutput:
        result = evaluate(args.expression)
        return CalculateOutput(
            expression=args.expression, result=result, display=format_result(result)
        )

    def make_incomplete(self, output: dict[str, Any]) -> dict[str, Any]:
        """A truncated response: the fractional part is lost."""
        result = float(math.trunc(output["result"]))
        return {**output, "result": result, "display": format_result(result)}

    def make_misleading(self, output: dict[str, Any], payload: str) -> dict[str, Any]:
        """A plausible but wrong number (10% high); the payload is not used."""
        result = round(output["result"] * 1.1, 2)
        return {**output, "result": result, "display": format_result(result)}

    def inject_instructions(self, output: dict[str, Any], payload: str) -> dict[str, Any]:
        return {**output, "display": append_text(output["display"], payload)}
