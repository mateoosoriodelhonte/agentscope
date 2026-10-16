"""Constrained types and fault-hook helpers shared by the planning tools.

Every identifier a model can pass is matched against a documented ASCII pattern (explicit
character classes, so Unicode digits never slip through) and every free-text string is
bounded at 1 KiB, in arguments and outputs alike. Fault hooks clip what they plant to the
same bound so a degraded output still validates.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import StringConstraints

MAX_TEXT = 1024

# Record ids, workstream ids, task ids: lower-case ASCII, digits, `_` and `-`.
# Lengths are stated alongside each pattern so the JSON Schema a model sees carries them.
RecordId = Annotated[str, StringConstraints(max_length=64, pattern=r"^[a-z0-9_-]{1,64}$")]
TeamName = Annotated[str, StringConstraints(max_length=32, pattern=r"^[a-z0-9-]{1,32}$")]
# ISO week, e.g. 2026-W44.
IsoWeek = Annotated[
    str,
    StringConstraints(max_length=8, pattern=r"^[0-9]{4}-W(0[1-9]|[1-4][0-9]|5[0-3])$"),
]
ShortText = Annotated[str, StringConstraints(min_length=1, max_length=200)]
Text = Annotated[str, StringConstraints(max_length=MAX_TEXT)]

RecordType = Literal["employee", "project"]


def clip(text: str, limit: int = MAX_TEXT) -> str:
    return text[:limit]


def append_text(text: str, payload: str, limit: int = MAX_TEXT) -> str:
    """`text` followed by `payload`, trimming the original text first so the planted payload
    survives and the result stays within `limit`."""
    if not text:
        return clip(payload, limit)
    joined = f"{text}\n\n{payload}"
    if len(joined) <= limit:
        return joined
    keep = limit - len(payload) - 2
    return f"{text[:keep]}\n\n{payload}" if keep > 0 else clip(payload, limit)
