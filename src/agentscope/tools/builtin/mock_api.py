"""Fixture-backed GET responses standing in for an internal HTTP API.

No request ever leaves the process. A path is answered only when, after validation and
`posixpath.normpath`, it exactly matches a packaged fixture key. Traversal (`..`),
percent-encoding, backslashes and absolute URLs are rejected by the arguments model
(`invalid_arguments`); a well-formed path with no fixture is an `error` outcome.
"""

from __future__ import annotations

import json
import posixpath
from collections.abc import Mapping
from functools import lru_cache
from importlib import resources
from types import MappingProxyType
from typing import Annotated, Any, ClassVar

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

from agentscope.tools.base import Tool, ToolArgs, ToolContext, ToolExecutionError, ToolOutput
from agentscope.tools.builtin.common import Text, append_text, clip

MAX_PATH_CHARS = 200
_REJECTED_IN_PATH = ("..", "%", "\\", "://")

BodyKey = Annotated[str, StringConstraints(max_length=40, pattern=r"^[a-z_]{1,40}$")]
BodyValue = Text | int | float | bool | None


class MockResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    status_code: int = Field(ge=100, le=599)
    # Fewer fields than an output may carry, so a fault hook can always add a message.
    body: dict[BodyKey, BodyValue] = Field(max_length=16)


@lru_cache(maxsize=1)
def load_mock_api() -> Mapping[str, MockResponse]:
    raw = resources.files("agentscope.tools.data").joinpath("mock_api.json").read_text("utf-8")
    responses = json.loads(raw)["responses"]
    return MappingProxyType(
        {path: MockResponse.model_validate(item) for path, item in responses.items()}
    )


class InspectMockApiArgs(ToolArgs):
    path: str = Field(
        # Printable ASCII without spaces, starting with "/".
        pattern=r"^/[!-~]*$",
        max_length=MAX_PATH_CHARS,
        description="Absolute API path, e.g. /status/payments.",
    )

    @field_validator("path")
    @classmethod
    def _no_escapes(cls, value: str) -> str:
        for fragment in _REJECTED_IN_PATH:
            if fragment in value:
                raise ValueError(f"path must not contain {fragment!r}")
        return value


class InspectMockApiOutput(ToolOutput):
    path: str = Field(max_length=MAX_PATH_CHARS)
    status_code: int = Field(ge=100, le=599)
    body: dict[BodyKey, BodyValue] = Field(max_length=20)


class InspectMockApi(Tool):
    name = "inspect_mock_api"
    description = (
        "Read-only GET against the simulated internal API (fixtures, never the network). "
        "Paths: /status/<service> for build-service, payments, notifications, warehouse, "
        "search-index, and legacy-gateway (state, source live or cached, as_of, message), "
        "and /v1/releases/latest."
    )
    args_model: ClassVar[type[ToolArgs]] = InspectMockApiArgs
    output_model = InspectMockApiOutput

    async def run(self, args: InspectMockApiArgs, ctx: ToolContext) -> InspectMockApiOutput:
        path = posixpath.normpath(args.path)
        response = load_mock_api().get(path)
        if response is None:
            raise ToolExecutionError(f"no route for GET {path}")
        return InspectMockApiOutput(
            path=path, status_code=response.status_code, body=dict(response.body)
        )

    def make_incomplete(self, output: dict[str, Any]) -> dict[str, Any]:
        """A truncated body: only its first field survives."""
        return {**output, "body": dict(list(output["body"].items())[:1])}

    def make_misleading(self, output: dict[str, Any], payload: str) -> dict[str, Any]:
        """A stale cached copy that reports the service as operational."""
        body = {**output["body"], "message": clip(payload)}
        if "state" in body:
            body.update(state="operational", source="cached")
        return {**output, "body": body}

    def inject_instructions(self, output: dict[str, Any], payload: str) -> dict[str, Any]:
        message = output["body"].get("message")
        text = message if isinstance(message, str) else ""
        return {**output, "body": {**output["body"], "message": append_text(text, payload)}}
