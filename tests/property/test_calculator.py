"""Properties of the safe calculator: total over arbitrary text, bounded, fast, and equal to
Python's own arithmetic on the expressions it accepts."""

from __future__ import annotations

import ast
import contextlib
import math
import operator
import time
from collections.abc import Callable
from typing import Any

import pytest
from hypothesis import assume, given, settings
from hypothesis import strategies as st
from pydantic import ValidationError

from agentscope.tools.builtin.calculator import (
    MAX_DEPTH,
    MAX_EXPRESSION_CHARS,
    MAX_MAGNITUDE,
    MAX_NODES,
    CalculateArgs,
    CalculationError,
    evaluate,
)

ARITHMETIC_ALPHABET = "0123456789.eE+-*/%() "

adversarial = st.one_of(
    st.text(max_size=MAX_EXPRESSION_CHARS + 50),
    st.text(alphabet=ARITHMETIC_ALPHABET, max_size=MAX_EXPRESSION_CHARS),
    st.integers(1, 100).map(lambda n: "**".join(["9"] * n)),
    st.integers(1, 100).map(lambda n: "(" * n + "1" + ")" * n),
    st.integers(1, 200).map(lambda n: "-" * n + "1"),
    st.integers(1, 66).map(lambda n: " + ".join(["999999"] * n)),
)


def tree_stats(text: str) -> tuple[int, int]:
    tree = ast.parse(text, mode="eval")

    def depth(node: ast.AST) -> int:
        return 1 + max((depth(c) for c in ast.iter_child_nodes(node)), default=0)

    return len(list(ast.walk(tree))), depth(tree)


@given(text=adversarial)
@settings(max_examples=500, deadline=None)
def test_total_bounded_and_fast_on_arbitrary_text(text: str) -> None:
    started = time.perf_counter()
    try:
        result = evaluate(text)
    except CalculationError:
        result = None
    assert time.perf_counter() - started < 0.05
    if result is not None:
        assert math.isfinite(result)
        assert abs(result) <= MAX_MAGNITUDE
        assert len(text) <= MAX_EXPRESSION_CHARS
        nodes, depth = tree_stats(text)
        assert nodes <= MAX_NODES
        assert depth <= MAX_DEPTH


@given(text=adversarial)
@settings(max_examples=200, deadline=None)
def test_arguments_model_raises_only_validation_errors(text: str) -> None:
    with contextlib.suppress(ValidationError):
        CalculateArgs.model_validate({"expression": text})


names = st.from_regex(r"[A-Za-z_][A-Za-z0-9_]{0,8}", fullmatch=True)
small = st.integers(0, 99).map(str)
NON_ARITHMETIC = (
    "{name}",
    "{name}()",
    "{name}({num})",
    "{num} + {name}",
    "({num}).{name}",
    "{name}[{num}]",
    "{name}.{name}({num})",
    "'{name}'",
    "[{num}]",
    "{{{num}}}",
    "lambda: {num}",
    "{num} if {num} else {num}",
    "{num} and {num}",
    "not {num}",
    "{num} < {num}",
    "~{num}",
    "{num} @ {num}",
    "{num} << {num}",
    "[{name} for {name} in ()]",
    "({name} := {num})",
    "__import__('{name}')",
    "f'{{{num}}}'",
)


@given(template=st.sampled_from(NON_ARITHMETIC), name=names, num=small)
@settings(max_examples=400)
def test_never_evaluates_anything_but_arithmetic(template: str, name: str, num: str) -> None:
    with pytest.raises(CalculationError):
        evaluate(template.format(name=name, num=num))


# --- equality with Python arithmetic ---------------------------------------------------

Tree = Any  # a number literal, ("neg"|"pos", Tree) or (op, Tree, Tree)
BINARY: dict[str, Callable[[Any, Any], Any]] = {
    "+": operator.add,
    "-": operator.sub,
    "*": operator.mul,
    "/": operator.truediv,
    "//": operator.floordiv,
    "%": operator.mod,
    "**": operator.pow,
}
literals = st.one_of(
    st.integers(-1000, 1000),
    st.floats(-1000, 1000, allow_nan=False, allow_infinity=False).map(lambda x: round(x, 3)),
)
trees = st.recursive(
    literals,
    lambda inner: st.one_of(
        st.tuples(st.sampled_from(["neg", "pos"]), inner),
        st.tuples(st.sampled_from(sorted(BINARY)), inner, inner),
    ),
    max_leaves=10,
)


def render(tree: Tree) -> str:
    if not isinstance(tree, tuple):
        return f"({tree!r})" if str(tree).startswith("-") else repr(tree)
    if len(tree) == 2:
        return f"({'-' if tree[0] == 'neg' else '+'}{render(tree[1])})"
    return f"({render(tree[1])} {tree[0]} {render(tree[2])})"


class Rejected(Exception):
    pass


def reference(tree: Tree, margins: list[bool]) -> Any:
    """Python's own int/float arithmetic under the documented bounds. Appends False to
    `margins` whenever a value lands close enough to a bound that float rounding could
    decide the outcome."""

    def check(value: Any) -> Any:
        if isinstance(value, complex) or not math.isfinite(value) or abs(value) > MAX_MAGNITUDE:
            raise Rejected
        if abs(value) > MAX_MAGNITUDE / 10:
            margins.append(False)
        return value

    if not isinstance(tree, tuple):
        return check(tree)
    if len(tree) == 2:
        value = reference(tree[1], margins)
        return check(-value if tree[0] == "neg" else +value)
    left, right = reference(tree[1], margins), reference(tree[2], margins)
    if tree[0] == "**":
        if abs(left) > 1e6 or abs(right) > 10:
            raise Rejected
        if abs(left) > 0.99e6 or abs(right) > 9.99 or right != int(right):
            margins.append(False)  # fractional exponents may round differently
    try:
        return check(BINARY[tree[0]](left, right))
    except (ZeroDivisionError, OverflowError):
        raise Rejected from None


@given(tree=trees)
@settings(max_examples=500, deadline=None)
def test_matches_python_arithmetic(tree: Tree) -> None:
    text = render(tree)
    nodes, depth = tree_stats(text)
    assume(len(text) <= MAX_EXPRESSION_CHARS and nodes <= MAX_NODES and depth <= MAX_DEPTH)
    margins: list[bool] = []
    try:
        expected = reference(tree, margins)
    except Rejected:
        expected = None
    try:
        actual: float | None = evaluate(text)
    except CalculationError:
        actual = None
    if expected is not None and actual is not None:
        assert math.isclose(actual, expected, rel_tol=1e-9, abs_tol=1e-9), text
    elif all(margins):
        assert (actual is None) == (expected is None), text
