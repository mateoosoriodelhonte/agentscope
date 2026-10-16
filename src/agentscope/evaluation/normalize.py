"""Text normalization and type-strict value comparison for deterministic checks."""

from __future__ import annotations

import math
import re
import unicodedata
from typing import Any

NORMALIZER_VERSION = "1"

_WHITESPACE = re.compile(r"\s+")
_EDGE_PUNCT = re.compile(r"^[\W_]+|[\W_]+$", re.UNICODE)
_NUMBER = re.compile(r"^[+-]?(?:\d+(?:\.\d+)?|\.\d+)(?:[eE][+-]?\d+)?$")


def normalize(text: str) -> str:
    """NFKC, case-folded, Cf characters removed, whitespace collapsed, edge punctuation
    stripped. Versioned as NORMALIZER_VERSION because results depend on it."""
    text = unicodedata.normalize("NFKC", text)
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Cf")
    text = _WHITESPACE.sub(" ", text.casefold()).strip()
    return _EDGE_PUNCT.sub("", text)


def is_number(value: Any) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


def as_number(value: Any) -> float | None:
    """A JSON number, or a string that is entirely a number. Booleans are never numbers."""
    if is_number(value):
        number = float(value)
    elif isinstance(value, str) and _NUMBER.match(value.strip().replace(",", "")):
        number = float(value.strip().replace(",", ""))
    else:
        return None
    return number if math.isfinite(number) else None


def strict_equal(a: Any, b: Any) -> bool:
    """Equality that never treats True as 1 or 1 as 1.0-with-a-different-type surprise."""
    if isinstance(a, bool) or isinstance(b, bool):
        return type(a) is type(b) and a == b
    if is_number(a) and is_number(b):
        return float(a) == float(b)
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
