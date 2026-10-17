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
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, ValidationError

from agentscope.tracing.redaction import sanitize_text

MAX_DECISION_CHARS = 100_000
# Deeper than any agent output schema; past it, redaction truncates what is stored anyway.
MAX_DECISION_DEPTH = 32
_THINK_OPEN = re.compile(r"<think>", re.IGNORECASE)
_THINK_CLOSE = re.compile(r"</think>", re.IGNORECASE)
# Characters that matter to the brace scanner outside and inside JSON strings.
_OUTSIDE = re.compile(r'[{}\[\]"]')
_INSIDE = re.compile(r'["\\]')

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


def _object_end(text: str, start: int) -> tuple[int, int] | None:
    """End (exclusive) and nesting depth of the balanced {...} opening at `start`, or None
    when it never closes. Respects JSON strings, so braces, fences and think tags inside
    string values are content, not structure."""
    braces = nesting = deepest = 0
    pos = start
    while (match := _OUTSIDE.search(text, pos)) is not None:
        char, pos = match.group(), match.end()
        if char == '"':
            while True:
                inner = _INSIDE.search(text, pos)
                if inner is None:
                    return None
                pos = inner.end()
                if inner.group() == '"':
                    break
                pos += 1  # a backslash escapes the next character
        elif char in "{[":
            nesting += 1
            deepest = max(deepest, nesting)
            if char == "{":
                braces += 1
        else:
            nesting -= 1
            if char == "}":
                braces -= 1
                if braces == 0:
                    return pos, deepest
    return None


def _candidates(text: str) -> Iterator[tuple[int, int, int]]:
    """(start, end, depth) of each balanced top-level {...} in order, skipping closed
    <think>...</think> blocks between them.

    Linear time: every search resumes where the previous one stopped, and once a <think>
    has no closing tag none after it can have one either, so think skipping stops there.
    """
    pos = 0
    brace = text.find("{")
    think = _THINK_OPEN.search(text)
    while brace >= 0:
        if think is not None and think.start() < brace:
            close = _THINK_CLOSE.search(text, think.end())
            if close is None:
                think = None
                continue
            pos = close.end()
            think = _THINK_OPEN.search(text, pos)
            if brace < pos:
                brace = text.find("{", pos)
            continue
        found = _object_end(text, brace)
        if found is None:
            # It never closes. Stop rather than rescan from every later brace, which is
            # quadratic on `{{{{...`; such a response is not a valid decision anyway.
            return
        end, depth = found
        yield brace, end, depth
        pos = end
        brace = text.find("{", pos)
        if think is not None and think.start() < pos:
            think = _THINK_OPEN.search(text, pos)


def _without_think(text: str) -> str:
    """`text` without closed <think>...</think> blocks (linear, like `_candidates`)."""
    parts = []
    pos = 0
    while (opening := _THINK_OPEN.search(text, pos)) is not None:
        close = _THINK_CLOSE.search(text, opening.end())
        if close is None:
            break
        parts.append(text[pos : opening.start()])
        pos = close.end()
    parts.append(text[pos:])
    return "".join(parts)


def _decision_object(content: str) -> dict[str, Any]:
    """The first top-level JSON object in a response that parses.

    The response is read as written: a decision that opens the response wins outright, and
    code fences or think tags matter only around objects, never inside their strings (a
    final answer may well contain a fenced shell command). Preambles such as a think block,
    prose or a ```json fence are skipped because only top-level objects are candidates.
    """
    first_error: DecisionError | None = None
    for start, end, depth in _candidates(content):
        error: DecisionError
        if depth > MAX_DECISION_DEPTH:
            error = DecisionError(
                "too_large", f"response nests deeper than {MAX_DECISION_DEPTH} levels"
            )
        else:
            try:
                data = json.loads(content[start:end])
            except RecursionError:  # backstop; the depth check above should prevent it
                error = DecisionError("too_large", "response nests too deeply")
            except json.JSONDecodeError as exc:
                error = DecisionError("not_json", f"response is not valid JSON ({exc.msg})")
            except ValueError:  # e.g. an integer longer than Python converts
                error = DecisionError("not_json", "response is not valid JSON (bad number)")
            else:
                if isinstance(data, dict):
                    return data
                error = DecisionError("not_object", "response JSON is not an object")
        first_error = first_error or error
    if first_error is not None:
        raise first_error
    if not _without_think(content).strip():
        raise DecisionError("empty", "response was empty")
    raise DecisionError("not_json", "response contains no JSON object")


def _sanitized(value: Any) -> Any:
    """NUL and unpaired surrogates replaced in every string and key: they cannot be stored
    (PostgreSQL rejects NUL, strict UTF-8 rejects lone surrogates) or serialized back."""
    if isinstance(value, str):
        return sanitize_text(value)
    if isinstance(value, dict):
        return {sanitize_text(k): _sanitized(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_sanitized(item) for item in value]
    return value


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
    if not content.strip():
        raise DecisionError("empty", "response was empty")
    data = _sanitized(_decision_object(content))
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
