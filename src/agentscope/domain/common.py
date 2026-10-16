"""Shared primitives for the domain contracts."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, StringConstraints

# Lower-case identifiers for agents and tools: safe in URLs, file names, and span names.
Identifier = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{0,63}$")]
# Human-assigned identifiers for test cases, versions, and datasets.
Slug = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")]


class StrictModel(BaseModel):
    """Base for every contract: unknown fields are rejected, never silently dropped, and
    non-finite floats (NaN, Infinity) are refused so hashes and JSON stay portable."""

    model_config = ConfigDict(extra="forbid", validate_default=True, allow_inf_nan=False)


def utcnow() -> datetime:
    return datetime.now(UTC)


def canonical_json(value: Any) -> str:
    """Deterministic JSON encoding used for hashing.

    Sorted keys, no insignificant whitespace, UTF-8, and no silent coercion: values that are
    not plain JSON (or NaN/Infinity) raise instead of being stringified.
    """
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    )


def content_hash(value: Any) -> str:
    """Stable content address, e.g. for dataset versions and agent configurations."""
    digest = hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()
    return f"sha256:{digest}"
