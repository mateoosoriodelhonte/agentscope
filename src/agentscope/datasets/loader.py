"""Hardened dataset loading.

Datasets are untrusted input: imported over the API, shared between people, checked into
repositories. Bytes become a `Dataset` only through this module, which bounds resource use
and refuses JSON that different parsers (or reviewers) could read differently:

- at most 5 MiB per file, never reading more than that plus one byte, and at most 200 JSON
  files per directory; symlinks that resolve outside the directory are skipped;
- UTF-8 only; duplicate object keys, NaN/Infinity (including numbers that overflow to
  infinity), unpaired surrogates, and nesting deeper than 32 levels are rejected;
- validation is strict, so "5" is never coerced to 5.

Every failure is a `DatasetError` whose issues carry JSON pointers to the offending field.

Directory convention (`agentscope eval --dataset DIR` relies on it; see tests/fixtures/README.md):

- ``*.dataset.json``: dataset files. Together they form one dataset: cases are concatenated
  in file-name order and every file must carry the same header (schema_version, name,
  version, description, agent). Each file is a complete dataset on its own.
- ``gate.json``: regression-gate thresholds (`GateThresholds`).
- ``*.baseline.json``: baselines, written only by ``agentscope eval --write-baseline``.

Any other ``*.json`` file is an error rather than being ignored, so a misnamed dataset file
cannot silently shrink a gated suite. Subdirectories are not scanned.
"""

from __future__ import annotations

import json
import logging
import math
import os
import re
import stat
from collections.abc import Iterator
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ValidationError
from pydantic_core import ErrorDetails

from agentscope.datasets.issues import DatasetError, Issue, json_pointer
from agentscope.domain.datasets import MAX_CASES, Dataset
from agentscope.domain.evaluation import GateThresholds

MAX_FILE_BYTES = 5 * 1024 * 1024
MAX_DIR_JSON_FILES = 200
MAX_DEPTH = 32
DATASET_SUFFIX = ".dataset.json"
BASELINE_SUFFIX = ".baseline.json"
GATE_FILE = "gate.json"
# Enough to fix a file in one pass without letting a hostile input flood the output.
_MAX_ISSUES = 50
_HEADER_FIELDS = ("schema_version", "name", "version", "description", "agent")

FileKind = Literal["dataset", "gate", "baseline"]

_log = logging.getLogger(__name__)


def classify(name: str) -> FileKind | None:
    """What a JSON file in a dataset directory is, judged by its name alone."""
    if name == GATE_FILE:
        return "gate"
    if name.endswith(DATASET_SUFFIX) and name != DATASET_SUFFIX:
        return "dataset"
    if name.endswith(BASELINE_SUFFIX) and name != BASELINE_SUFFIX:
        return "baseline"
    return None


def load_dataset_bytes(data: bytes) -> Dataset:
    """Parse and strictly validate one dataset document."""
    text, document = _decode(data)
    # Literal[1] accepts true and 1.0 even in strict mode; a version marker must be exact.
    if isinstance(document, dict) and type(document.get("schema_version", 1)) is not int:
        raise DatasetError([Issue("/schema_version", "must be the integer 1")])
    return _validate(Dataset, text)


def load_dataset_file(path: Path) -> Dataset:
    try:
        return load_dataset_bytes(_read_file(path))
    except DatasetError as exc:
        raise _from_file(exc, path) from None


def load_dataset_dir(path: Path) -> Dataset:
    """Load every ``*.dataset.json`` in a directory as one dataset (see the module docstring)."""
    root = _resolve_dir(path)
    issues: list[Issue] = []
    parts: list[tuple[str, Dataset]] = []
    for entry in _json_files(root):
        kind = classify(entry.name)
        if kind is None:
            issues.append(
                Issue(
                    "",
                    f"unrecognized JSON file; expected *{DATASET_SUFFIX}, {GATE_FILE} or "
                    f"*{BASELINE_SUFFIX}",
                    source=entry.name,
                )
            )
        elif kind == "dataset":
            try:
                parts.append((entry.name, load_dataset_file(entry)))
            except DatasetError as exc:
                issues.extend(exc.issues)
    if issues:
        raise DatasetError(issues[:_MAX_ISSUES])
    if not parts:
        raise DatasetError([Issue("", f"no *{DATASET_SUFFIX} files in {path}")])
    return _merge(parts)


def load_gate_file(path: Path) -> GateThresholds:
    """Read ``gate.json`` with the same limits and strictness as dataset files."""
    try:
        text, _document = _decode(_read_file(path))
        return _validate(GateThresholds, text)
    except DatasetError as exc:
        raise _from_file(exc, path) from None


# --- reading ------------------------------------------------------------------------------


def _os_error(exc: OSError) -> str:
    return exc.strerror or type(exc).__name__


def _read_file(path: Path) -> bytes:
    # O_NONBLOCK so that a FIFO cannot stall the open; it is then refused as non-regular.
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0))
    except OSError as exc:
        raise DatasetError([Issue("", f"cannot open: {_os_error(exc)}")]) from None
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise DatasetError([Issue("", "not a regular file")])
        with os.fdopen(fd, "rb", closefd=False) as handle:
            return handle.read(MAX_FILE_BYTES + 1)
    finally:
        os.close(fd)


def _resolve_dir(path: Path) -> Path:
    root = Path(os.path.realpath(path))
    if not root.is_dir():
        raise DatasetError([Issue("", f"{path} is not a directory")])
    return root


def _json_files(root: Path) -> list[Path]:
    """Top-level ``*.json`` entries in name order, minus symlinks that escape `root`."""
    names: list[str] = []
    try:
        with os.scandir(root) as entries:
            for entry in entries:
                if entry.name.endswith(".json"):
                    names.append(entry.name)
                    if len(names) > MAX_DIR_JSON_FILES:
                        raise DatasetError(
                            [Issue("", f"more than {MAX_DIR_JSON_FILES} JSON files in {root}")]
                        )
    except OSError as exc:
        raise DatasetError([Issue("", f"cannot list {root}: {_os_error(exc)}")]) from None
    files: list[Path] = []
    for name in sorted(names):
        candidate = root / name
        # realpath never raises on symlink loops; a looping link then fails to open.
        if candidate.is_symlink() and not Path(os.path.realpath(candidate)).is_relative_to(root):
            _log.warning("skipping %s: symlink resolves outside %s", name, root)
            continue
        files.append(candidate)
    return files


def _from_file(exc: DatasetError, path: Path) -> DatasetError:
    return DatasetError([replace(issue, source=issue.source or path.name) for issue in exc.issues])


# --- parsing ------------------------------------------------------------------------------


class _RepeatedKeys(dict[str, Any]):
    """A JSON object that repeated keys. Reported with its location after parsing, because a
    later duplicate can silently replace a value a reviewer saw (e.g. empty a list)."""

    repeated: tuple[str, ...] = ()


@dataclass(frozen=True)
class _NonFinite:
    literal: str


def _object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    obj: dict[str, Any] = {}
    repeated: list[str] = []
    for key, value in pairs:
        if key in obj:
            repeated.append(key)
        obj[key] = value
    if not repeated:
        return obj
    flagged = _RepeatedKeys(obj)
    flagged.repeated = tuple(repeated)
    return flagged


def _float(literal: str) -> float | _NonFinite:
    value = float(literal)
    return value if math.isfinite(value) else _NonFinite(literal)


def _decode(data: bytes) -> tuple[str, Any]:
    """UTF-8 text and its parsed JSON document, or a DatasetError."""
    if len(data) > MAX_FILE_BYTES:
        raise DatasetError([Issue("", f"larger than {MAX_FILE_BYTES} bytes")])
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise DatasetError([Issue("", f"not valid UTF-8 (byte {exc.start})")]) from None
    try:
        document = json.loads(
            text, object_pairs_hook=_object, parse_constant=_NonFinite, parse_float=_float
        )
    except RecursionError:
        raise DatasetError([Issue("", f"nested deeper than {MAX_DEPTH} levels")]) from None
    except json.JSONDecodeError as exc:
        raise DatasetError(
            [Issue("", f"invalid JSON: {exc.msg} (line {exc.lineno}, column {exc.colno})")]
        ) from None
    except ValueError as exc:  # e.g. an integer literal beyond Python's digit limit
        raise DatasetError([Issue("", f"invalid JSON: {str(exc)[:200]}")]) from None
    issues = _inspect(document)
    if issues:
        raise DatasetError(issues)
    return text, document


_SURROGATE = re.compile("[\ud800-\udfff]")

# A location as a linked list of (parent, key): O(1) to extend while walking, turned into a
# JSON pointer only when an issue is reported.
type _Link = tuple[_Link, str | int] | None


def _pointer(link: _Link) -> str:
    keys: list[str | int] = []
    while link is not None:
        link, key = link
        keys.append(key)
    return json_pointer(reversed(keys))


def _inspect(document: Any) -> list[Issue]:
    """Depth, duplicate keys, non-finite numbers, and surrogates, located by JSON pointer.

    An explicit stack of iterators visits the document in order without recursion, so a
    document the parser accepted cannot exhaust the Python stack here.
    """
    issues: list[Issue] = []

    def check_leaf(value: Any, link: _Link) -> None:
        if type(value) is str and _SURROGATE.search(value):
            issues.append(Issue(_pointer(link), "string contains an unpaired surrogate"))
        elif type(value) is _NonFinite:
            issues.append(
                Issue(_pointer(link), f"non-finite number {value.literal} is not allowed")
            )

    def entries(node: dict[str, Any] | list[Any], link: _Link) -> Iterator[tuple[Any, Any]]:
        if isinstance(node, list):
            return enumerate(node)
        if isinstance(node, _RepeatedKeys):
            issues.extend(Issue(_pointer((link, k)), "duplicate key") for k in node.repeated)
        for key in node:
            if _SURROGATE.search(key):
                issues.append(Issue(_pointer((link, key)), "key contains an unpaired surrogate"))
        return iter(node.items())

    if not isinstance(document, dict | list):
        check_leaf(document, None)
        return issues
    stack: list[tuple[Iterator[tuple[Any, Any]], _Link, int]] = [(entries(document, None), None, 1)]
    while stack and len(issues) < _MAX_ISSUES:
        items, link, depth = stack[-1]
        for key, value in items:
            if isinstance(value, dict | list):
                child = (link, key)
                if depth < MAX_DEPTH:
                    stack.append((entries(value, child), child, depth + 1))
                    break
                issues.append(Issue(_pointer(child), f"nested deeper than {MAX_DEPTH} levels"))
            else:
                check_leaf(value, (link, key))
        else:
            stack.pop()
    return issues[:_MAX_ISSUES]


def _validate[M: BaseModel](model: type[M], text: str) -> M:
    try:
        return model.model_validate_json(text, strict=True)
    except ValidationError as exc:
        errors = exc.errors(include_url=False, include_input=False)
        raise DatasetError([_issue(e) for e in errors[:_MAX_ISSUES]]) from None


def _issue(error: ErrorDetails) -> Issue:
    loc = list(error["loc"])
    message = error["msg"].removeprefix("Value error, ")
    if loc and loc[-1] == "[key]":  # pydantic's marker for an invalid dict key
        loc.pop()
        message = f"invalid key: {message}"
    return Issue(json_pointer(loc), message)


# --- directories ------------------------------------------------------------------------


def _merge(parts: list[tuple[str, Dataset]]) -> Dataset:
    first_name, first = parts[0]
    issues: list[Issue] = []
    owner: dict[str, str] = {}
    cases = []
    for name, part in parts:
        for field in _HEADER_FIELDS:
            if getattr(part, field) != getattr(first, field):
                issues.append(
                    Issue(
                        f"/{field}",
                        f"differs from {first_name}; dataset files in one directory must "
                        "share the same header",
                        source=name,
                    )
                )
        for index, case in enumerate(part.cases):
            if case.id in owner:
                issues.append(
                    Issue(
                        f"/cases/{index}/id",
                        f"duplicate case id {case.id!r} (also in {owner[case.id]})",
                        source=name,
                    )
                )
            owner.setdefault(case.id, name)
            cases.append(case)
    if len(cases) > MAX_CASES:
        issues.append(Issue("/cases", f"{len(cases)} cases in total; at most {MAX_CASES}"))
    if issues:
        raise DatasetError(issues[:_MAX_ISSUES])
    return Dataset.model_validate({**first.model_dump(exclude={"cases"}), "cases": cases})
