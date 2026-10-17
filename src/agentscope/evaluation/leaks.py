"""Finds forbidden substrings in model-controlled text, including trivially encoded copies.

Plain matching runs on `fold`ed text (NFKC, case-folded, Cf removed), with all whitespace
removed from both the text and the substring, so case changes, zero-width characters,
compatibility forms and spacing ("AS - CANARY", a line break mid-word) cannot hide a
substring. This is the folding the dataset lint applies when it vets canaries.

Encoded copies are found by encoding the substring itself (as written, folded, lower- and
upper-cased): base64 (standard and URL-safe, at every byte alignment, so it is found inside
a longer encoded message), hex (any case), and percent-encoding (by decoding the text).
Encoded forms shorter than `MIN_ENCODED_CHARS` are not searched, because such short
fragments match ordinary text. Object keys are model-controlled text too, so they are
searched like string values.
"""

from __future__ import annotations

import base64
import re
import unicodedata
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any
from urllib.parse import unquote

from agentscope.evaluation.normalize import fold, fold_unspaced, strip_format_chars

MIN_ENCODED_CHARS = 8
_ANY_WHITESPACE = re.compile(r"\s+")
_URLSAFE_TO_STD = str.maketrans("-_", "+/")


@dataclass(frozen=True)
class Needle:
    """One forbidden substring, prepared for every supported encoding."""

    index: int
    plain: str  # folded, without whitespace
    base64_forms: tuple[str, ...]
    hex_forms: tuple[str, ...]

    @classmethod
    def build(cls, index: int, substring: str) -> Needle:
        # Encoding is case-sensitive, so the common case variants are encoded as well.
        variants = (substring, fold(substring), substring.lower(), substring.upper())
        raws = [v.encode("utf-8") for v in dict.fromkeys(variants) if v]
        base64_forms = (f for raw in raws for f in _base64_fragments(raw))
        hex_forms = (raw.hex() for raw in raws)
        return cls(
            index=index,
            plain=fold_unspaced(substring),
            base64_forms=tuple(
                f for f in dict.fromkeys(base64_forms) if len(f) >= MIN_ENCODED_CHARS
            ),
            hex_forms=tuple(f for f in dict.fromkeys(hex_forms) if len(f) >= MIN_ENCODED_CHARS),
        )


def _base64_fragments(raw: bytes) -> list[str]:
    """The base64 characters fully determined by `raw`, for each of the three byte offsets
    it can start at inside a longer message."""
    fragments = []
    for offset in range(3):
        encoded = base64.b64encode(b"\0" * offset + raw).decode("ascii").rstrip("=")
        first = -(-8 * offset // 6)  # ceil: first char made only of `raw` bits
        last = 8 * (offset + len(raw)) // 6  # exclusive: chars that need no following bits
        fragments.append(encoded[first:last])
    return fragments


def _compact(text: str) -> str:
    """Text as an encoder would have produced it: no Cf characters, no whitespace."""
    return _ANY_WHITESPACE.sub("", unicodedata.normalize("NFKC", strip_format_chars(text)))


def find_encoding(text: str, needle: Needle) -> str | None:
    """Return how `needle` occurs in `text` (plain, percent, base64, hex), or None."""
    if needle.plain and needle.plain in fold_unspaced(text):
        return "plain"
    if needle.plain and "%" in text and needle.plain in fold_unspaced(unquote(text)):
        return "percent"
    compact = _compact(text)
    standard = compact.translate(_URLSAFE_TO_STD)
    if any(form in compact or form in standard for form in needle.base64_forms):
        return "base64"
    folded_compact = compact.casefold()
    if any(form in folded_compact for form in needle.hex_forms):
        return "hex"
    return None


def string_leaves(value: Any, pointer: str = "") -> Iterator[tuple[str, str]]:
    """Yield (location, text) for every string in a JSON value: each string leaf at its
    JSON pointer, and each object key at its member's pointer suffixed with " (key)"."""
    if isinstance(value, str):
        yield pointer or "/", value
    elif isinstance(value, dict):
        for key, item in value.items():
            member = f"{pointer}/{str(key).replace('~', '~0').replace('/', '~1')}"
            yield f"{member} (key)", str(key)
            yield from string_leaves(item, member)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from string_leaves(item, f"{pointer}/{index}")
