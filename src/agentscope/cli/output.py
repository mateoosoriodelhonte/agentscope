"""Terminal-safe output helpers.

Trace and dataset strings are untrusted: control characters and bidi overrides are
stripped before anything reaches the terminal, and Rich markup is never interpreted.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

from rich.table import Table
from rich.text import Text

_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f‪-‮⁦-⁩]")


def clean_text(value: str) -> str:
    return _CONTROL.sub("", value)


def safe(value: object, style: str = "") -> Text:
    """Untrusted text as a Rich renderable: never parsed as markup (a bare str would be)."""
    return Text(clean_text(str(value)), style=style)


def table(*columns: str, keys: int = 1) -> Table:
    """A borderless table whose first `keys` columns (identifiers people copy) are never
    wrapped or cut short on a narrow terminal; the other columns shrink instead."""
    grid = Table(box=None, header_style="bold", pad_edge=False)
    for index, column in enumerate(columns):
        grid.add_column(column, no_wrap=index < keys)
    return grid


def _dumps(payload: Any) -> str:
    # ensure_ascii keeps control bytes and bidi overrides escaped.
    return json.dumps(payload, ensure_ascii=True, sort_keys=True, indent=2) + "\n"


def emit_json(payload: Any) -> None:
    """Write one JSON document to stdout."""
    sys.stdout.write(_dumps(payload))


def write_json(path: Path, payload: Any) -> None:
    path.write_text(_dumps(payload), encoding="utf-8")
