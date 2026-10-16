"""The structured decision protocol between the runtime and a model.

The wire schema is a flat object — `{type, tool, arguments, output, rationale}` — rather
than a root-level union, because constrained decoders (Ollama's grammar conversion,
OpenAI-compatible JSON modes) handle flat objects reliably. `tool` is a free string, not an
enum of permitted tools, so an unauthorized request stays observable; rejecting it is the
runtime guard's job.
"""

from __future__ import annotations

import copy
import json
import re
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, ValidationError

MAX_DECISION_CHARS = 100_000
_THINK = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)

DecisionErrorKind = Literal["empty", "not_json", "not_object", "schema", "too_large"]


class _WireDecision(BaseModel):
    # Lenient on unknown keys (models add them); the dropped keys are reported.
    model_config = ConfigDict(extra="ignore", allow_inf_nan=False)

    type: Literal["tool_call", "final"]
    tool: str | None = Field(default=None, max_length=64)
    arguments: dict[str, JsonValue] | None = None
    output: dict[str, JsonValue] | None = None
    rationale: str = Field(default="", max_length=4000)


@dataclass(frozen=True)
class ToolCallDecision:
    tool: str
    arguments: dict[str, JsonValue]
    rationale: str
    extra_keys: tuple[str, ...] = ()


@dataclass(frozen=True)
class FinalDecision:
    output: dict[str, JsonValue]
    rationale: str
    extra_keys: tuple[str, ...] = ()


class DecisionError(ValueError):
    def __init__(self, kind: DecisionErrorKind, message: str) -> None:
        super().__init__(message)
        self.kind = kind


def _first_object(text: str) -> str | None:
    """Return the first balanced top-level {...} span, respecting JSON strings."""
    start = text.find("{")
    if start < 0:
        return None
    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start : index + 1]
    return None


def _describe(error: ValidationError) -> str:
    """Location and error type only — never echo the offending value back to the model."""
    parts = []
    for item in error.errors(include_url=False, include_input=False)[:4]:
        location = ".".join(str(p) for p in item["loc"]) or "(root)"
        parts.append(f"{location}: {item['type']}")
    return "; ".join(parts)


def parse_decision(content: str) -> ToolCallDecision | FinalDecision:
    if len(content) > MAX_DECISION_CHARS:
        raise DecisionError("too_large", f"response exceeds {MAX_DECISION_CHARS} characters")
    text = _THINK.sub("", content).strip()
    if not text:
        raise DecisionError("empty", "response was empty")
    fenced = _FENCE.search(text)
    if fenced:
        text = fenced.group(1).strip()
    candidate = _first_object(text)
    if candidate is None:
        raise DecisionError("not_json", "response contains no JSON object")
    try:
        data = json.loads(candidate)
    except json.JSONDecodeError as exc:
        raise DecisionError("not_json", f"response is not valid JSON ({exc.msg})") from exc
    if not isinstance(data, dict):
        raise DecisionError("not_object", "response JSON is not an object")
    try:
        wire = _WireDecision.model_validate(data, strict=True)
    except ValidationError as exc:
        raise DecisionError("schema", _describe(exc)) from exc
    extra = tuple(sorted(set(data) - set(_WireDecision.model_fields)))
    if wire.type == "tool_call":
        if not wire.tool:
            raise DecisionError("schema", "tool: missing for type=tool_call")
        return ToolCallDecision(wire.tool, wire.arguments or {}, wire.rationale, extra)
    if wire.output is None:
        raise DecisionError("schema", "output: missing for type=final")
    return FinalDecision(wire.output, wire.rationale, extra)


def inline_refs(schema: dict[str, Any]) -> dict[str, Any]:
    """Resolve local `$ref`s so providers with limited JSON Schema support can use it."""
    defs = schema.get("$defs", {})

    def resolve(node: Any, depth: int = 0) -> Any:
        if depth > 32:
            raise ValueError("schema nesting too deep")
        if isinstance(node, dict):
            ref = node.get("$ref")
            if isinstance(ref, str) and ref.startswith("#/$defs/"):
                return resolve(copy.deepcopy(defs[ref.removeprefix("#/$defs/")]), depth + 1)
            return {k: resolve(v, depth + 1) for k, v in node.items() if k != "$defs"}
        if isinstance(node, list):
            return [resolve(item, depth + 1) for item in node]
        return node

    resolved: dict[str, Any] = resolve(schema)
    return resolved


def decision_schema(output_schema: dict[str, Any]) -> dict[str, Any]:
    """JSON Schema for one decision: either a tool call or the final structured output."""
    return {
        "type": "object",
        "properties": {
            "type": {"type": "string", "enum": ["tool_call", "final"]},
            "tool": {"type": ["string", "null"], "description": "Tool name when type=tool_call."},
            "arguments": {
                "type": ["object", "null"],
                "description": "Tool arguments when type=tool_call.",
            },
            "output": {
                "anyOf": [inline_refs(output_schema), {"type": "null"}],
                "description": "Final answer when type=final.",
            },
            "rationale": {"type": "string", "description": "One or two sentences of reasoning."},
        },
        "required": ["type", "rationale"],
        "additionalProperties": False,
    }
