"""Evaluation dataset contracts.

Imported datasets are untrusted input: every model rejects unknown fields, strings and
collections are bounded, and matchers are plain comparisons (no user-supplied regex).
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal, Self

from pydantic import Field, JsonValue, model_validator

from agentscope.domain.common import Identifier, Slug, StrictModel, content_hash
from agentscope.domain.tools import FaultSpec

MAX_CASES = 2000
FieldPath = str  # dotted path into the final output, e.g. "answer" or "plan.total_hours"


class MatchMode(StrEnum):
    EXACT = "exact"
    NORMALIZED = "normalized"  # case-folded, punctuation and whitespace collapsed
    CONTAINS = "contains"  # normalized substring
    NUMERIC = "numeric"  # |actual - expected| <= tolerance
    ONE_OF = "one_of"  # normalized equality with any listed value


class ExpectedAnswer(StrictModel):
    field: FieldPath = Field(
        default="answer", pattern=r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z0-9_]+)*$"
    )
    match: MatchMode = MatchMode.NORMALIZED
    value: JsonValue = None
    values: list[JsonValue] | None = Field(default=None, max_length=50)
    tolerance: float | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if self.match is MatchMode.ONE_OF:
            if not self.values:
                raise ValueError("match 'one_of' requires a non-empty 'values' list")
        elif self.values is not None:
            raise ValueError("'values' is only valid with match 'one_of'")
        if self.match is MatchMode.NUMERIC and not isinstance(self.value, int | float):
            raise ValueError("match 'numeric' requires a numeric 'value'")
        if self.tolerance is not None and self.match is not MatchMode.NUMERIC:
            raise ValueError("'tolerance' is only valid with match 'numeric'")
        return self


class ArgMatcher(StrictModel):
    """Exactly one of the comparison fields must be set."""

    equals: JsonValue = None
    contains: str | None = Field(default=None, max_length=500)
    one_of: list[JsonValue] | None = Field(default=None, max_length=50)

    @model_validator(mode="after")
    def _exactly_one(self) -> Self:
        set_fields = [
            name for name in ("contains", "one_of") if getattr(self, name) is not None
        ] + (["equals"] if "equals" in self.model_fields_set else [])
        if len(set_fields) != 1:
            raise ValueError("argument matcher needs exactly one of: equals, contains, one_of")
        return self


class ExpectedToolCall(StrictModel):
    tool: Identifier
    arguments: dict[Identifier, ArgMatcher] = Field(default_factory=dict, max_length=20)
    min_calls: int = Field(default=1, ge=1, le=20)


OutputStatus = Literal["answered", "insufficient_evidence", "refused", "planned"]


class SuccessCriteria(StrictModel):
    must_complete: bool = True
    # Expected value of the final output's `status` field, when the agent reports one.
    expected_output_status: OutputStatus | None = None
    # For cases with injected tool failures: True = the agent must recover (retry or
    # alternative tool) and still complete; False = it must degrade gracefully instead of
    # fabricating an answer. None = no expectation.
    expect_recovery: bool | None = None
    max_steps: int | None = Field(default=None, ge=1, le=50)


class LimitsOverride(StrictModel):
    max_steps: int | None = Field(default=None, ge=1, le=50)
    tool_timeout_s: float | None = Field(default=None, gt=0, le=60)
    run_timeout_s: float | None = Field(default=None, gt=0, le=1800)


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
    forbidden_tools: list[Identifier] = Field(default_factory=list, max_length=32)
    forbidden_output_substrings: list[str] = Field(default_factory=list, max_length=20)
    faults: list[FaultSpec] = Field(default_factory=list, max_length=10)
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
        return content_hash(self.model_dump(mode="json"))
