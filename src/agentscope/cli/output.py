"""Terminal-safe output helpers.

Trace and dataset strings are untrusted: control characters and bidi overrides are
stripped before anything reaches the terminal, and Rich markup is never interpreted.
"""

from __future__ import annotations

import json
import re
import sys
from typing import Any

_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f‪-‮⁦-⁩]")


def clean_text(value: str) -> str:
    return _CONTROL.sub("", value)


def emit_json(payload: Any) -> None:
    """Write one JSON document to stdout (ensure_ascii keeps control bytes escaped)."""
    sys.stdout.write(json.dumps(payload, ensure_ascii=True, sort_keys=True, indent=2) + "\n")
