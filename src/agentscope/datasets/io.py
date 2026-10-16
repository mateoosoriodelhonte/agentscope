"""Canonical dataset export and safe export file names."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import TypeAdapter

from agentscope.datasets.loader import DATASET_SUFFIX
from agentscope.domain.common import Slug
from agentscope.domain.datasets import Dataset

_SLUG: TypeAdapter[str] = TypeAdapter(Slug)


def export_dataset(dataset: Dataset) -> bytes:
    """Canonical pretty JSON: every field (defaults included), keys sorted, two-space indent,
    UTF-8, trailing newline.

    Equal datasets export to identical bytes, and loading an export gives back an equal
    dataset with the same content hash, so exports diff cleanly in review.
    """
    document: dict[str, Any] = dataset.model_dump(mode="json")
    for case in document["cases"]:
        for call in case["expected_tool_calls"]:
            call["arguments"] = {
                name: _single_comparison(matcher) for name, matcher in call["arguments"].items()
            }
    text = json.dumps(document, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False)
    return (text + "\n").encode("utf-8")


def _single_comparison(matcher: dict[str, Any]) -> dict[str, Any]:
    """An argument matcher takes exactly one comparison and an explicit `"equals": null`
    counts as one, so only the comparison in use is written."""
    for key in ("contains", "one_of"):
        if matcher[key] is not None:
            return {key: matcher[key]}
    return {"equals": matcher["equals"]}


def export_filename(dataset: Dataset) -> str:
    """``<name>@<version>.dataset.json``, built only from re-validated slugs (no separators,
    no leading dot), so it is a plain file name on every platform."""
    name = _SLUG.validate_python(dataset.name, strict=True)
    version = _SLUG.validate_python(dataset.version, strict=True)
    return f"{name}@{version}{DATASET_SUFFIX}"


def export_path(output_dir: Path, dataset: Dataset) -> Path:
    """Where to write `dataset` inside `output_dir`; refuses anything that resolves elsewhere
    (for example an existing symlink with the same name)."""
    root = output_dir.resolve()
    target = (root / export_filename(dataset)).resolve()
    if target.parent != root:
        raise ValueError(f"export path for {dataset.name!r} resolves outside {root}")
    return target
