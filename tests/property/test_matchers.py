"""Properties of the argument matchers and the text normalizer they rely on."""

from __future__ import annotations

import time
from typing import Any

import pytest
from hypothesis import assume, given, settings
from hypothesis import strategies as st

from agentscope.domain.datasets import ArgMatcher
from agentscope.evaluation.matchers import argument_matches, value_matches
from agentscope.evaluation.normalize import (
    contains_normalized,
    equal_normalized,
    fold,
    normalize,
)

scalars = (
    st.none()
    | st.booleans()
    | st.integers(min_value=-(10**9), max_value=10**9)
    | st.floats(allow_nan=False, allow_infinity=False)
    | st.text(max_size=20)
)
json_values = st.recursive(
    scalars,
    lambda inner: (
        st.lists(inner, max_size=3) | st.dictionaries(st.text(max_size=5), inner, max_size=3)
    ),
    max_leaves=8,
)
needles = st.text(min_size=1, max_size=50)


def json_type(value: Any) -> str:
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int | float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return "null"


@given(st.text())
@settings(max_examples=1000)
def test_normalization_is_idempotent(text: str) -> None:
    assert fold(fold(text)) == fold(text)
    assert normalize(normalize(text)) == normalize(text)


@given(
    st.text(max_size=40),
    st.integers(min_value=0, max_value=40),
    st.sampled_from(["\u200b", "\u200d", "\u2060", "\ufeff", "\u00ad", "\u202e"]),
)
def test_format_characters_cannot_hide_text(text: str, position: int, hidden: str) -> None:
    spliced = text[:position] + hidden + text[position:]
    assert fold(spliced) == fold(text)
    assert normalize(spliced) == normalize(text)


@given(json_values)
def test_equals_is_reflexive(value: Any) -> None:
    assert value_matches(ArgMatcher(equals=value), value)


@given(json_values, json_values)
def test_equals_never_crosses_json_types(expected: Any, actual: Any) -> None:
    assume(json_type(expected) != json_type(actual))
    assert not value_matches(ArgMatcher(equals=expected), actual)


@given(st.integers(min_value=-2, max_value=2))
def test_booleans_are_not_numbers(number: int) -> None:
    for flag in (True, False):
        assert not value_matches(ArgMatcher(equals=number), flag)
        assert not value_matches(ArgMatcher(equals=flag), number)
        assert not value_matches(ArgMatcher(equals=[number]), [flag])
        assert not value_matches(ArgMatcher(one_of=[number]), flag)
    assert not value_matches(ArgMatcher(equals=number), str(number))
    assert not value_matches(ArgMatcher(one_of=[str(number)]), number)


@given(needles, json_values)
def test_contains_only_looks_inside_strings(needle: str, value: Any) -> None:
    assume(not isinstance(value, str))
    assert not value_matches(ArgMatcher(contains=needle), value)


@given(needles)
def test_contains_matches_its_own_text(needle: str) -> None:
    assume(normalize(needle))
    assert value_matches(ArgMatcher(contains=needle), needle)


@given(
    st.text(alphabet="abcXYZ019 -", min_size=1, max_size=30), st.text(alphabet="abc ", max_size=5)
)
def test_contains_ignores_case_and_spacing(needle: str, padding: str) -> None:
    assume(normalize(needle))
    variant = f"{padding} {needle.swapcase().replace(' ', '   ')} {padding}"
    assert value_matches(ArgMatcher(contains=needle), variant)


@given(
    st.lists(st.text(st.characters(codec="ascii"), max_size=20), min_size=1, max_size=5), st.data()
)
def test_one_of_normalizes_strings(options: list[str], data: st.DataObject) -> None:
    chosen = data.draw(st.sampled_from(options))
    assert value_matches(ArgMatcher(one_of=list(options)), f"  {chosen.upper()}  ")


@given(
    st.sampled_from(["equals", "contains", "one_of"]),
    st.dictionaries(st.text(max_size=5), json_values),
)
def test_missing_argument_never_matches(kind: str, arguments: dict[str, Any]) -> None:
    matcher = ArgMatcher.model_validate(
        {kind: {"equals": None, "contains": "x", "one_of": [None]}[kind]}
    )
    assert not argument_matches(
        matcher, {k: v for k, v in arguments.items() if k != "absent"}, "absent"
    )


@given(
    st.text(alphabet="/?.-_!#", min_size=1, max_size=5), st.text(alphabet="abc 123", max_size=20)
)
def test_punctuation_only_needles_are_not_wildcards(needle: str, text: str) -> None:
    # Normalizing "/" would leave "", which is a substring of everything.
    assert not value_matches(ArgMatcher(contains=needle), text)
    assert value_matches(ArgMatcher(contains=needle), f"{text}{needle}{text}")


@given(st.text(alphabet=" \t\n", min_size=1, max_size=3), st.text(max_size=20))
def test_blank_needles_never_match(needle: str, text: str) -> None:
    assert not value_matches(ArgMatcher(contains=needle), text)


def test_punctuation_only_options_keep_their_punctuation() -> None:
    root = ArgMatcher(one_of=["/"])
    assert value_matches(root, "/")
    for other in ("", "/..", "?", "-"):
        assert not value_matches(root, other), other


@given(st.integers(min_value=2**53, max_value=2**80))
def test_large_integers_compare_exactly(number: int) -> None:
    assert value_matches(ArgMatcher(equals=number), number)
    assert not value_matches(ArgMatcher(equals=number), number + 1)
    assert not value_matches(ArgMatcher(one_of=[number]), number + 1)


def test_integers_beyond_the_float_range_do_not_crash() -> None:
    huge = 10**400
    assert not value_matches(ArgMatcher(equals=1.0), huge)
    assert not value_matches(ArgMatcher(equals=huge), 1e308)
    assert value_matches(ArgMatcher(equals=huge), huge)
    assert value_matches(ArgMatcher(equals=3), 3.0)  # one JSON number type


@given(st.text(alphabet="-+#%$°*~/_&@", min_size=1, max_size=5), st.text(max_size=20))
def test_signs_and_symbols_are_significant(symbols: str, text: str) -> None:
    """Only sentence punctuation, quotes and brackets are trimmed: "-3" is not "3"."""
    assume(normalize(text) and text.strip() == text)
    assert not equal_normalized(f"{symbols}{text}", text)
    assert not equal_normalized(f"{text}{symbols}", text)


@pytest.mark.parametrize(
    ("a", "b"),
    [
        ("1987.", "1987"),
        ('"Halden & Roe"', "Halden & Roe"),
        ("(1987)", "1987"),
        ("¿Sí?", "sí"),
        ("«Amara Quill»", "amara quill"),
        ("  ...September!  ", "september"),
        ("\u2018Sept\u2019", "sept"),
    ],
)
def test_sentence_punctuation_quotes_and_brackets_are_trimmed(a: str, b: str) -> None:
    assert equal_normalized(a, b)


@pytest.mark.parametrize("run", ["!", ".", "-", " ", "!?.,;: "])
def test_normalization_is_linear_in_the_length_of_a_punctuation_run(run: str) -> None:
    """A word, a long punctuation run, a word: a backtracking edge regex rescans the run from
    every position (quadratic), which froze the API on a hostile expected value."""
    text = "a" + run * (100_000 // len(run)) + "a"
    started = time.perf_counter()
    normalize(text)
    equal_normalized(text, text)
    contains_normalized(text, text)
    assert time.perf_counter() - started < 1.0
