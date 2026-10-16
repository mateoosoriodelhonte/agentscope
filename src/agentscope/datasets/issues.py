"""Problems found while loading or linting a dataset."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Literal

Severity = Literal["error", "warning"]


def json_pointer(parts: Iterable[str | int]) -> str:
    """RFC 6901 pointer for a location; "" is the whole document.

    Keys that cannot be encoded (unpaired surrogates) are backslash-escaped so that the
    pointer itself can always be printed or serialized.
    """
    tokens = (
        str(p).encode("utf-8", "backslashreplace").decode("utf-8").replace("~", "~0") for p in parts
    )
    return "".join("/" + token.replace("/", "~1") for token in tokens)


@dataclass(frozen=True)
class Issue:
    path: str  # RFC 6901 JSON pointer into the document, e.g. /cases/3/expected/value
    message: str
    severity: Severity = "error"
    # The file the pointer refers to, when a dataset spans a directory.
    source: str | None = None

    def __str__(self) -> str:
        where = f"{self.source}#{self.path}" if self.source else (self.path or "(document)")
        return f"{where}: {self.message}"


class DatasetError(ValueError):
    """Invalid dataset input: the CLI exits with code 2 and the API answers 422."""

    def __init__(self, issues: Sequence[Issue]) -> None:
        if not issues:
            raise ValueError("DatasetError needs at least one issue")
        self.issues = tuple(issues)
        shown = "; ".join(str(i) for i in self.issues[:3])
        more = len(self.issues) - 3
        super().__init__(shown + (f"; and {more} more" if more > 0 else ""))
