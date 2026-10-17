"""Evaluation dataset contracts.

Imported datasets are untrusted input: every model rejects unknown fields, strings and
collections are bounded, and matchers are plain comparisons (no user-supplied regex).
Every field of `EvalCase` (other than id, title, task, and tags) is consumed by exactly one
deterministic check or by the runner; see docs/EVALUATION.md.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal, Self

from pydantic import Field, JsonValue, model_validator

from agentscope.domain.common import Identifier, Slug, StrictModel, content_hash
from agentscope.domain.tools import FaultSpec, ModelFaultSpec

MAX_CASES = 2000


class MatchMode(StrEnum):
    EXACT = "exact"  # type-strict equality
    # NFKC, case-folded, whitespace collapsed, sentence punctuation, quotes and brackets
    # trimmed at the edges (signs and symbols such as "-", "+", "#" and "%" are kept)
    NORMALIZED = "normalized"
    CONTAINS = "contains"  # normalized substring of a scalar answer (bounded by max_chars)
    NUMERIC = (
        "numeric"  # |actual - expected| <= tolerance; actual must be a number or numeric string
    )
    ONE_OF = "one_of"  # normalized (strings) or type-strict (others) equality with an option


class ExpectedAnswer(StrictModel):
    field: str = Field(
        default="answer", max_length=200, pattern=r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z0-9_]+)*$"
    )
    match: MatchMode = MatchMode.NORMALIZED
    value: JsonValue = None
    values: list[JsonValue] | None = Field(default=None, min_length=1, max_length=32)
    tolerance: float | None = Field(default=None, ge=0)
    # For `contains`: reject answers longer than this, so listing every candidate cannot pass.
    max_chars: int | None = Field(default=None, ge=1, le=5000)

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if self.match is MatchMode.ONE_OF:
            if not self.values:
                raise ValueError("match 'one_of' requires a non-empty 'values' list")
        elif self.values is not None:
            raise ValueError("'values' is only valid with match 'one_of'")
        if self.match is MatchMode.NUMERIC and (
            isinstance(self.value, bool) or not isinstance(self.value, int | float)
        ):
            raise ValueError("match 'numeric' requires a numeric 'value'")
        if self.tolerance is not None and self.match is not MatchMode.NUMERIC:
            raise ValueError("'tolerance' is only valid with match 'numeric'")
        if self.max_chars is not None and self.match is not MatchMode.CONTAINS:
            raise ValueError("'max_chars' is only valid with match 'contains'")
        if self.match in (MatchMode.CONTAINS, MatchMode.NORMALIZED) and not isinstance(
            self.value, str
        ):
            raise ValueError(f"match '{self.match.value}' requires a string 'value'")
        return self


class ArgMatcher(StrictModel):
    """Exactly one of the comparison fields must be set."""

    equals: JsonValue = None
    contains: str | None = Field(default=None, min_length=1, max_length=500)
    one_of: list[JsonValue] | None = Field(default=None, min_length=1, max_length=32)

    @model_validator(mode="after")
    def _exactly_one(self) -> Self:
        chosen = [name for name in ("contains", "one_of") if getattr(self, name) is not None]
        if "equals" in self.model_fields_set:
            chosen.append("equals")
        if len(chosen) != 1:
            raise ValueError("argument matcher needs exactly one of: equals, contains, one_of")
        return self


class ExpectedToolCall(StrictModel):
    tool: Identifier
    # All matchers must be satisfied by the same executed call.
    arguments: dict[Identifier, ArgMatcher] = Field(default_factory=dict, max_length=20)
    min_calls: int = Field(default=1, ge=1, le=20)
    max_calls: int | None = Field(default=None, ge=1, le=50)

    @model_validator(mode="after")
    def _bounds(self) -> Self:
        if self.max_calls is not None and self.max_calls < self.min_calls:
            raise ValueError("max_calls must be >= min_calls")
        return self


OutputStatus = Literal["answered", "insufficient_evidence", "cannot_complete", "refused", "planned"]
# Output statuses that mean "the agent declined to give an answer" (graceful degradation).
DEGRADED_STATUSES = frozenset({"insufficient_evidence", "cannot_complete", "refused"})


class SuccessCriteria(StrictModel):
    # False lets a run end without an output (max_steps, timeout): schema_validity and
    # task_completion then do not hold it against the agent.
    must_complete: bool = True
    # Expected value of the final output's `status` field.
    expected_output_status: OutputStatus | None = None
    # For cases with injected tool failures: True = the agent must recover from every
    # failure (a later successful call to the failed tool or to a declared recovery tool)
    # and answer; False = it must degrade gracefully (a declining status, no answer) instead
    # of fabricating one.
    expect_recovery: bool | None = None
    # Tools whose later success also counts as recovering from a failed tool call. Only
    # these: a successful call to some unrelated tool does not replace the failed one.
    recovery_tools: list[Identifier] = Field(default_factory=list, max_length=32)
    # Budgets judged by task_completion (distinct from the runtime's hard limits).
    max_steps_budget: int | None = Field(default=None, ge=1, le=50)
    max_tool_calls: int | None = Field(default=None, ge=0, le=100)


class LimitsOverride(StrictModel):
    """Per-case limits. They can only tighten the agent's own limits, never loosen them."""

    max_steps: int | None = Field(default=None, ge=1, le=50)
    tool_timeout_s: float | None = Field(default=None, gt=0, le=30)
    run_timeout_s: float | None = Field(default=None, gt=0, le=600)


class EvalCase(StrictModel):
    """A single test case (named EvalCase so pytest does not try to collect it)."""

    id: Slug
    title: str = Field(default="", max_length=200)
    task: str = Field(min_length=1, max_length=4000)
    agent: Identifier | None = None
    expected: ExpectedAnswer | None = None
    permitted_tools: list[Identifier] | None = Field(default=None, max_length=32)
    expected_tool_calls: list[ExpectedToolCall] = Field(default_factory=list, max_length=20)
    tool_order: list[Identifier] = Field(default_factory=list, max_length=20)
    # When true, no other executed tool may appear between the ordered tools.
    tool_order_strict: bool = False
    forbidden_tools: list[Identifier] = Field(default_factory=list, max_length=32)
    forbidden_output_substrings: list[str] = Field(default_factory=list, max_length=20)
    faults: list[FaultSpec] = Field(default_factory=list, max_length=10)
    model_faults: list[ModelFaultSpec] = Field(default_factory=list, max_length=10)
    success_criteria: SuccessCriteria = Field(default_factory=SuccessCriteria)
    limits: LimitsOverride | None = None
    tags: list[Slug] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if self.permitted_tools is not None:
            overlap = set(self.permitted_tools) & set(self.forbidden_tools)
            if overlap:
                raise ValueError(f"tools both permitted and forbidden: {sorted(overlap)}")
        for substring in self.forbidden_output_substrings:
            if not 1 <= len(substring) <= 500:
                raise ValueError("forbidden_output_substrings entries must be 1-500 characters")
        return self

    def case_hash(self) -> str:
        return content_hash(_hash_view(self.model_dump(mode="json")))


class Dataset(StrictModel):
    schema_version: Literal[1] = 1
    name: Slug
    version: Slug = "1"
    description: str = Field(default="", max_length=4000)
    agent: Identifier | None = None
    cases: list[EvalCase] = Field(min_length=1, max_length=MAX_CASES)

    @model_validator(mode="after")
    def _unique_case_ids(self) -> Self:
        seen: set[str] = set()
        for case in self.cases:
            if case.id in seen:
                raise ValueError(f"duplicate case id: {case.id}")
            seen.add(case.id)
        return self

    def content_hash(self) -> str:
        document = self.model_dump(mode="json")
        document["cases"] = [_hash_view(case) for case in document["cases"]]
        return content_hash(document)


def _hash_view(case: dict[str, Any]) -> dict[str, Any]:
    """A dumped case as it is hashed. `success_criteria.recovery_tools` was added after
    datasets were first hashed, so it is left out while empty: cases written before it
    existed keep their hashes, and stored suites, baselines and comparisons still match."""
    criteria = case.get("success_criteria")
    if isinstance(criteria, dict) and not criteria.get("recovery_tools"):
        trimmed = {key: value for key, value in criteria.items() if key != "recovery_tools"}
        return {**case, "success_criteria": trimmed}
    return case
