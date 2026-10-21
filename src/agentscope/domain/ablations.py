"""Ablation contracts: attributing an A→B change to the configuration dimensions behind it.

A comparison that changes several dimensions at once is confounded: the pass-rate delta
cannot be credited to any one change. An ablation evaluates the same dataset version with
one dimension changed at a time, in both directions:

- forward, "A + d": A with only B's value for dimension d. Answers "is d alone enough?"
- backward, "B - d": B with only A's value for d. Answers "does B still need d?"

Effects are percentage points over the same cases, never relative percentages. The
interaction is the total delta minus the sum of the forward solo effects: how much of the
change only appears when dimensions are combined (positive) or cancel out (negative).

Pairs ("A + d1 + d2") locate that interaction: a pair's interaction is its effect minus both
solo effects, so two defences that only work together show up as one pair with a large
positive interaction while each alone shows none.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import Field

from agentscope.domain.common import StrictModel
from agentscope.domain.experiments import (
    CaseChange,
    ConfigDifference,
    ConfigDimension,
    SuiteSummary,
)

# Upper bound on dimensions ablated in one run (two suites each).
MAX_ABLATION_DIMENSIONS = 8
# Pairs are evaluated only up to this many dimensions (k choose 2 extra suites).
MAX_PAIRWISE_DIMENSIONS = 6


class AblationDirection(StrEnum):
    FORWARD = "forward"  # A plus one dimension from B
    BACKWARD = "backward"  # B minus one dimension (A's value restored)


class AblationVariant(StrictModel):
    """One variant configuration, evaluated on the full dataset."""

    dimension: ConfigDimension
    direction: AblationDirection
    # Configuration paths this variant takes from the other side, e.g. ["isolate_untrusted"].
    paths: list[str]
    agent_ref: str  # e.g. research_assistant@v1-with-isolation
    config_hash: str
    suite_id: str
    passed: int = Field(ge=0)
    total: int = Field(ge=0)
    harness_errors: int = Field(ge=0)
    # Forward: pass rate of A + d minus pass rate of A.
    # Backward: pass rate of B minus pass rate of B - d (what d contributes inside B).
    effect_pp: float
    # Forward: cases failing in A that pass in A + d. Backward: cases passing in B that
    # fail in B - d (B depends on d for them).
    gained: list[str] = Field(default_factory=list)
    # Forward: cases passing in A that fail in A + d. Backward: cases failing in B that
    # pass in B - d (d hurts them inside B).
    lost: list[str] = Field(default_factory=list)


class AblationPair(StrictModel):
    """A forward variant with two dimensions taken from B: "A + d1 + d2"."""

    dimensions: list[ConfigDimension] = Field(min_length=2, max_length=2)
    agent_ref: str
    config_hash: str
    suite_id: str
    passed: int = Field(ge=0)
    total: int = Field(ge=0)
    harness_errors: int = Field(ge=0)
    effect_pp: float  # pass rate of A + d1 + d2 minus pass rate of A
    # effect_pp minus both forward solo effects: what the two add only in combination.
    interaction_pp: float
    gained: list[str] = Field(default_factory=list)  # failing in A, passing here
    lost: list[str] = Field(default_factory=list)  # passing in A, failing here


class CaseAttribution(StrictModel):
    """Why one case's verdict differs between A and B."""

    case_id: str
    change: CaseChange  # REGRESSION or FIX
    # Applying d alone to A already gives B's verdict.
    sufficient: list[ConfigDimension] = Field(default_factory=list)
    # Removing d from B gives back A's verdict.
    necessary: list[ConfigDimension] = Field(default_factory=list)
    # True when no single dimension is sufficient: the change needs a combination.
    needs_combination: bool
    # When no single dimension is sufficient: the evaluated pairs that are.
    sufficient_pairs: list[list[ConfigDimension]] = Field(default_factory=list)
    # Run ids for this case, keyed "a", "b", "forward:<dimension>", "backward:<dimension>"
    # and "pair:<dimension>+<dimension>" (pair keys in configuration order).
    runs: dict[str, str] = Field(default_factory=dict)


class Ablation(StrictModel):
    ablation_id: str
    name: str
    created_at: datetime
    # The stored comparison this ablation explains, when it was started from one.
    experiment_id: str | None = None
    dataset_name: str
    dataset_version: str
    dataset_hash: str
    deterministic: bool  # every suite (A, B and all variants) used the fake provider
    a: SuiteSummary
    b: SuiteSummary
    dimensions: list[ConfigDimension]  # ablated dimensions, in configuration order
    # Differences no configuration field expresses (resolved content, environment): they
    # cannot be isolated by changing a field, so they are reported, not ablated.
    not_ablated: list[ConfigDifference] = Field(default_factory=list)
    variants: list[AblationVariant]
    # Forward pairs; empty when pairwise analysis was skipped (see pairs_skipped).
    pairs: list[AblationPair] = Field(default_factory=list)
    pairs_skipped: bool = False  # more than MAX_PAIRWISE_DIMENSIONS dimensions, or disabled
    total_effect_pp: float  # pass rate of B minus pass rate of A
    sum_of_solo_effects_pp: float  # sum of forward effects
    interaction_pp: float  # total_effect_pp - sum_of_solo_effects_pp
    n: int = Field(ge=0)  # cases compared (shared, same content, judged on every side)
    cases: list[CaseAttribution]  # only cases whose verdict differs between A and B
    warnings: list[str] = Field(default_factory=list)


class AblationListItem(StrictModel):
    ablation_id: str
    name: str
    created_at: datetime
    experiment_id: str | None
    dataset_name: str
    dataset_version: str
    a_ref: str
    b_ref: str
    dimensions: list[ConfigDimension]
    total_effect_pp: float
    interaction_pp: float
    deterministic: bool
