"""Text normalization and type-strict value comparison for deterministic checks."""

from __future__ import annotations

import math
import re
import unicodedata
from typing import Any

NORMALIZER_VERSION = "3"

_WHITESPACE = re.compile(r"\s+")
# Trimmed from the edges of a normalized answer: sentence punctuation, quotes and brackets
# (plus whitespace). Signs and symbols ("-3", "C++", "C#", "50%", "$5") and a leading
# decimal point (".5") are kept: trimming them would make different answers equal.
_EDGE_CHARS = frozenset(".,;:!?¡¿…'\"`。、")
_EDGE_CATEGORIES = frozenset({"Ps", "Pe", "Pi", "Pf"})  # brackets, initial/final quotes
_NUMBER = re.compile(r"^[+-]?(?:\d+(?:\.\d+)?|\.\d+)(?:[eE][+-]?\d+)?$")
# Thousands separators are accepted only in groups of three ("12,500"), so "1,5" is not 15.
_GROUPED_NUMBER = re.compile(r"^[+-]?\d{1,3}(?:,\d{3})+(?:\.\d+)?$")


def strip_format_chars(text: str) -> str:
    """Remove Unicode format characters (Cf: zero-width spaces, joiners, bidi controls),
    which are invisible and would otherwise let a string hide inside another."""
    return "".join(ch for ch in text if unicodedata.category(ch) != "Cf")


def fold(text: str) -> str:
    """Cf removed, NFKC, case-folded, whitespace collapsed and trimmed.

    Format characters go first so that removing them cannot create a new composable
    sequence, and NFKC runs again after case folding, so `fold` is idempotent.
    """
    text = unicodedata.normalize("NFKC", strip_format_chars(text))
    text = unicodedata.normalize("NFKC", text.casefold())
    return _WHITESPACE.sub(" ", text).strip()


def fold_unspaced(text: str) -> str:
    """`fold` without any whitespace, so spacing cannot hide one string inside another
    ("AS - CANARY", a line break mid-word). Normalized again because removing a space can
    put a combining mark next to the letter it belongs to."""
    return unicodedata.normalize("NFKC", _WHITESPACE.sub("", fold(text)))


def _is_edge(ch: str) -> bool:
    return ch in _EDGE_CHARS or ch.isspace() or unicodedata.category(ch) in _EDGE_CATEGORIES


def _leading_point(folded: str, i: int, end: int) -> bool:
    """A lone "." that starts a token (".5", ".NET", ".env"): a decimal point or part of a
    name, not sentence punctuation, so trimming it would make ".5" equal "5". An ellipsis
    ("...1987") is still punctuation."""
    return (
        folded[i] == "."
        and i + 1 < end
        and folded[i + 1].isalnum()
        and (i == 0 or folded[i - 1] != ".")
    )


def normalize(text: str) -> str:
    """`fold` plus sentence punctuation, quotes and brackets trimmed from both ends (a lone
    leading "." before a letter or digit is kept: see `_leading_point`). Versioned as
    NORMALIZER_VERSION because results depend on it.

    A plain scan from each end, so it is linear in the length of the text; an edge regex
    rescans a long punctuation run from every position, which is quadratic.
    """
    folded = fold(text)
    start, end = 0, len(folded)
    while start < end and _is_edge(folded[start]) and not _leading_point(folded, start, end):
        start += 1
    while end > start and _is_edge(folded[end - 1]):
        end -= 1
    return folded[start:end]


def _comparable(text: str) -> str:
    """`normalize`, except that text with no letter or digit keeps all its punctuation
    (folded), so "/", "/.." and "?" stay distinct from each other and from ""."""
    normalized = normalize(text)
    return normalized if any(ch.isalnum() for ch in normalized) else fold(text)


def equal_normalized(a: str, b: str) -> bool:
    return _comparable(a) == _comparable(b)


def contains_normalized(needle: str, text: str) -> bool:
    """`needle` occurs in `text`, both normalized.

    Edge punctuation is trimmed from the needle only when something is left, so a
    punctuation-only needle ("/", "?") is searched for as written instead of becoming ""
    and matching every string. A needle that is empty after folding never matches.
    """
    target = _comparable(needle)
    return bool(target) and target in fold(text)


def is_number(value: Any) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


def as_number(value: Any) -> float | None:
    """A JSON number, or a string that is entirely a number. Booleans are never numbers,
    and values too large for a float are not numbers either (they compare as None)."""
    if is_number(value):
        try:
            number = float(value)
        except OverflowError:  # an int beyond the float range
            return None
    elif isinstance(value, str):
        text = value.strip()
        if _GROUPED_NUMBER.match(text):
            text = text.replace(",", "")
        if not _NUMBER.match(text):
            return None
        number = float(text)
    else:
        return None
    return number if math.isfinite(number) else None


def strict_equal(a: Any, b: Any) -> bool:
    """Equality that never treats True as 1 or 1 as 1.0-with-a-different-type surprise."""
    if isinstance(a, bool) or isinstance(b, bool):
        return type(a) is type(b) and a == b
    if is_number(a) and is_number(b):
        # Python compares int with float exactly: 2**53 + 1 != 2.0**53, and a huge int
        # never overflows (converting both sides to float would do both wrong).
        return bool(a == b)
    if type(a) is not type(b):
        return False
    if isinstance(a, dict):
        return a.keys() == b.keys() and all(strict_equal(a[k], b[k]) for k in a)
    if isinstance(a, list):
        return len(a) == len(b) and all(strict_equal(x, y) for x, y in zip(a, b, strict=True))
    return bool(a == b)


def get_path(value: Any, path: str) -> tuple[bool, Any]:
    """Resolve a dotted path; returns (found, value)."""
    current = value
    for part in path.split("."):
        if isinstance(current, dict) and part in current:
            current = current[part]
        elif isinstance(current, list) and part.isdigit() and int(part) < len(current):
            current = current[int(part)]
        else:
            return False, None
    return True, current
