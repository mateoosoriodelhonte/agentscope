"""Shared CLI plumbing: consoles, exit codes, service construction and dataset loading.

Exit codes: 0 success · 1 checks or gate failed · 2 invalid input · 3 runtime/harness error.
"""

from __future__ import annotations

import os
from collections.abc import Sequence
from dataclasses import dataclass
from enum import IntEnum
from pathlib import Path
from typing import TYPE_CHECKING

import typer
from pydantic import ValidationError
from rich.console import Console
from rich.text import Text

from agentscope.cli.output import clean_text
from agentscope.datasets.issues import Issue

if TYPE_CHECKING:
    from agentscope.domain.datasets import Dataset
    from agentscope.services import Services

console = Console(highlight=False)
err = Console(stderr=True, highlight=False)

SIMULATED = "Simulated model: demonstrates harness mechanics, not model behaviour."


class Exit(IntEnum):
    OK = 0
    FAILED = 1
    INVALID = 2
    ERROR = 3


def fail(message: str, code: Exit, issues: Sequence[Issue] = ()) -> typer.Exit:
    err.print(Text(f"error: {clean_text(message)}", style="red"))
    for issue in issues[:50]:
        err.print(Text(f"  {clean_text(str(issue))}"))
    return typer.Exit(int(code))


def open_services(*, recover: bool = False) -> Services:
    """Build the shared engine; a database this release cannot use is a runtime error.

    `recover` marks work whose process is gone as interrupted, for commands that start work.
    Read-only commands leave it off: they must never change what another process is doing.
    """
    from sqlalchemy.exc import SQLAlchemyError

    from agentscope.config import get_settings
    from agentscope.services import build_services
    from agentscope.storage.db import DatabaseAheadError, StorageLocationError

    try:
        services = build_services()
        if recover:
            services.recover_interrupted()
        return services
    except DatabaseAheadError as exc:
        raise fail(str(exc), Exit.ERROR) from exc
    except StorageLocationError as exc:
        raise fail(storage_error(exc), Exit.ERROR) from exc
    except SQLAlchemyError as exc:
        location = get_settings().database_location
        raise fail(
            f"cannot use the database at {location} ({type(exc).__name__})", Exit.ERROR
        ) from exc


def storage_error(exc: Exception) -> str:
    """An actionable message for a database location the filesystem refuses."""
    from agentscope.config import get_settings

    return (
        f"cannot open the database at {get_settings().database_location}: {exc}; "
        "choose a writable location with AGENTSCOPE_DATA_DIR or AGENTSCOPE_DATABASE_URL"
    )


def concise(exc: ValidationError) -> str:
    """A validation error on one line: located messages, without pydantic's URLs or the
    submitted values."""
    parts = [
        f"{'.'.join(str(p) for p in error['loc']) or 'value'}: {error['msg']}"
        for error in exc.errors(include_url=False)[:5]
    ]
    return "; ".join(parts)


def check_writable(path: Path, option: str) -> None:
    """Raise ValueError unless `path` can be written. Checked before a suite runs, so a typo
    is invalid input (exit 2) instead of a failure that throws away the finished result."""
    if path.is_dir():
        raise ValueError(f"{option} {path}: is a directory")
    parent = path.parent
    if not parent.is_dir():
        raise ValueError(f"{option} {path}: directory {parent} does not exist")
    if not os.access(parent, os.W_OK) or (path.exists() and not os.access(path, os.W_OK)):
        raise ValueError(f"{option} {path}: not writable")


def load_path(path: Path) -> Dataset:
    """A dataset file, or a directory following the loader's convention."""
    from agentscope.datasets.loader import load_dataset_dir, load_dataset_file

    return load_dataset_dir(path) if path.is_dir() else load_dataset_file(path)


@dataclass(frozen=True)
class DatasetSource:
    dataset: Dataset
    directory: Path | None  # set when loaded from a dataset directory (gate.json, baselines)


def load_dataset(services: Services, spec: str) -> DatasetSource:
    """An existing path is read from disk; anything else is a stored NAME[@VERSION].
    Raises DatasetError or UnknownDatasetError."""
    path = Path(spec)
    if path.exists():
        return DatasetSource(load_path(path), path if path.is_dir() else None)
    name, _, version = spec.partition("@")
    return DatasetSource(services.stored_dataset(name, version or None), None)


def escapes(candidate: Path, directory: Path) -> bool:
    """A symlink in `directory` that resolves outside it."""
    if not candidate.is_symlink():
        return False
    target = Path(os.path.realpath(candidate))
    return not target.is_relative_to(Path(os.path.realpath(directory)))
