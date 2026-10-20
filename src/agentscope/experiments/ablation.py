"""Ablation attribution: which configuration dimensions explain the change from A to B.

Pure: this module plans the variant configurations (forward "A + d", backward "B - d" and
forward pairs "A + d1 + d2") and attributes the change from their suite results; running the
suites is the services layer's job. A dimension is ablated by copying the top-level
configuration fields that express it (experiments.compare.DIMENSIONS) from one side to the
other. What no such field expresses (the agent itself, resolved content, the AgentScope
version or tool data) cannot be changed on its own, so it is reported as not ablated.

Effects are percentage points over the compared cases, computed with exact rational
arithmetic and converted to floats only when they enter the contract, so an interaction is
exactly the effect minus the solo effects, not that difference plus rounding noise.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from fractions import Fraction
from itertools import combinations

from agentscope.domain.ablations import (
    Ablation,
    AblationDirection,
    AblationPair,
    AblationVariant,
    CaseAttribution,
)
from agentscope.domain.agents import AgentConfig
from agentscope.domain.common import utcnow
from agentscope.domain.evaluation import CaseResult, SuiteResult
from agentscope.domain.experiments import CaseChange, ConfigDimension, SuiteSummary
from agentscope.experiments.compare import (
    DIMENSIONS,
    classify,
    config_diff,
    environment_diff,
    suite_diff,
)

FORWARD, BACKWARD = AblationDirection.FORWARD, AblationDirection.BACKWARD
# AgentConfig.version is a Slug: at most 64 characters.
_MAX_VERSION = 64
_FLIPS = (CaseChange.REGRESSION, CaseChange.FIX)
_UNCOMPARABLE = (CaseChange.CONTENT_CHANGED, CaseChange.HARNESS_ERROR)


def ablatable_dimensions(a: AgentConfig, b: AgentConfig) -> dict[ConfigDimension, tuple[str, ...]]:
    """The dimensions A and B differ in, each with the top-level fields expressing it, in
    configuration order (the order config_diff lists their fields). A field outside
    DIMENSIONS (the agent itself, dimension `other`) is never ablatable, and identity fields
    (version, label, description) never count as differences."""
    fields: dict[ConfigDimension, list[str]] = {}
    for difference in config_diff(a, b):
        field = difference.path.split(".")[0]
        dimension = DIMENSIONS.get(field)
        if dimension is None:
            continue
        paths = fields.setdefault(dimension, [])
        if field not in paths:
            paths.append(field)
    return {dimension: tuple(paths) for dimension, paths in fields.items()}


@dataclass(frozen=True)
class Variant:
    """A configuration to evaluate: A with one or two dimensions taken from B (forward), or
    B with one dimension's fields restored from A (backward)."""

    direction: AblationDirection
    dimensions: tuple[ConfigDimension, ...]
    paths: tuple[str, ...]
    config: AgentConfig

    @property
    def is_pair(self) -> bool:
        return len(self.dimensions) == 2

    @property
    def key(self) -> str:
        """How CaseAttribution.runs names this variant."""
        if self.is_pair:
            return "pair:" + "+".join(self.dimensions)
        return f"{self.direction.value}:{self.dimensions[0]}"


def _version(base: str, suffix: str, taken: set[str]) -> str:
    """`base` + `suffix` as an unused Slug: the base is cut to fit (the suffix, which says
    what the variant is, never is), and a counter is appended should the name be taken."""
    tail, n = suffix, 1
    version = base[: _MAX_VERSION - len(tail)] + tail
    while version in taken:
        n += 1
        tail = f"{suffix}-{n}"
        version = base[: _MAX_VERSION - len(tail)] + tail
    taken.add(version)
    return version


def _variant(
    base: AgentConfig,
    other: AgentConfig,
    direction: AblationDirection,
    dimensions: tuple[ConfigDimension, ...],
    fields: Mapping[ConfigDimension, tuple[str, ...]],
    taken: set[str],
) -> Variant:
    paths = tuple(path for dimension in dimensions for path in fields[dimension])
    forward = direction is FORWARD
    sign = " + " if forward else " - "
    data = base.model_dump(mode="json")
    replacement = other.model_dump(mode="json")
    data.update({path: replacement[path] for path in paths})
    data.update(
        version=_version(
            base.version, ("-with-" if forward else "-without-") + "-".join(dimensions), taken
        ),
        label=("A" if forward else "B") + sign + sign.join(dimensions),
        description=f"Ablation variant: {base.ref} with {', '.join(paths)} from {other.ref}.",
    )
    # Validated again: every value comes from a valid configuration, but the name is new.
    return Variant(direction, dimensions, paths, AgentConfig.model_validate(data))


def plan_variants(
    a: AgentConfig,
    b: AgentConfig,
    fields: Mapping[ConfigDimension, tuple[str, ...]],
    *,
    pairs: bool = True,
) -> list[Variant]:
    """For each dimension in `fields` (see ablatable_dimensions) its forward then its
    backward variant, then with `pairs` every forward pair, all in configuration order.
    Versions read like `v1-with-isolation`, `v2-without-isolation` and
    `v1-with-isolation-prompt`, and are unique: never A's, B's or another variant's."""
    taken = {a.version, b.version}
    variants = []
    for dimension in fields:
        variants.append(_variant(a, b, FORWARD, (dimension,), fields, taken))
        variants.append(_variant(b, a, BACKWARD, (dimension,), fields, taken))
    if pairs:
        variants += [
            _variant(a, b, FORWARD, pair, fields, taken) for pair in combinations(fields, 2)
        ]
    return variants


@dataclass(frozen=True)
class _Outcome:
    """A variant's verdict on each compared case. None where it has no agent verdict (a
    harness error, or a case missing or changed in the variant's suite): that counts as not
    passed in pass counts, and is never evidence for or against a dimension."""

    variant: Variant
    suite: SuiteResult
    verdicts: dict[str, bool | None]
    runs: dict[str, str]  # case id -> run id

    @property
    def passed(self) -> int:
        return sum(verdict is True for verdict in self.verdicts.values())

    @property
    def unjudged(self) -> int:
        return sum(verdict is None for verdict in self.verdicts.values())


def _outcome(variant: Variant, suite: SuiteResult, compared: Mapping[str, CaseResult]) -> _Outcome:
    cases = {case.case_id: case for case in suite.cases}
    verdicts: dict[str, bool | None] = {}
    runs: dict[str, str] = {}
    for case_id, reference in compared.items():
        case = cases.get(case_id)
        if case is None or case.case_hash != reference.case_hash or case.harness_error is not None:
            verdicts[case_id] = None
        else:
            verdicts[case_id] = case.passed
        if case is not None and case.run_id is not None:
            runs[case_id] = case.run_id
    return _Outcome(variant, suite, verdicts, runs)


def _pp(part: int, whole: int) -> Fraction:
    return Fraction(100 * part, whole) if whole else Fraction(0)


def _flips(
    reference: Mapping[str, bool], verdicts: Mapping[str, bool | None]
) -> tuple[list[str], list[str]]:
    """Cases failing in `reference` that pass in `verdicts`, and cases passing in `reference`
    that do not (a missing verdict is not a pass, matching the pass counts)."""
    up = [case for case, passed in reference.items() if not passed and verdicts[case] is True]
    down = [case for case, passed in reference.items() if passed and verdicts[case] is not True]
    return up, down


def _summary(result: SuiteResult) -> SuiteSummary:
    return SuiteSummary(
        suite_id=result.suite_id,
        agent_ref=result.agent_config.ref,
        config_hash=result.config_hash,
        effective_config_hash=result.effective_config_hash,
        dataset_hash=result.dataset_hash,
        metrics=result.metrics,
    )


def attribute(
    a: SuiteResult,
    b: SuiteResult,
    evaluated: Sequence[tuple[Variant, SuiteResult]],
    *,
    name: str,
    ablation_id: str | None = None,
    experiment_id: str | None = None,
    pairs_skipped: bool = False,
    warnings: Sequence[str] = (),
    created_at: datetime | None = None,
) -> Ablation:
    """Attribute the change from suite `a` to suite `b` with the variants in `evaluated`
    (from plan_variants, each with its suite on the same dataset version).

    Compared cases are those A and B share with unchanged content and an agent verdict on
    both sides; every count and effect is over them. Only cases whose verdict differs between
    A and B (regressions and fixes) are attributed.
    """
    a_cases = {case.case_id: case for case in a.cases}
    b_cases = {case.case_id: case for case in b.cases}
    shared = sorted(a_cases.keys() & b_cases.keys())
    compared = {
        case_id: a_cases[case_id]
        for case_id in shared
        if classify(a_cases[case_id], b_cases[case_id]) not in _UNCOMPARABLE
    }
    n = len(compared)
    a_passed = {case_id: case.passed for case_id, case in compared.items()}
    b_passed = {case_id: b_cases[case_id].passed for case_id in compared}
    pa, pb = sum(a_passed.values()), sum(b_passed.values())
    outcomes = [_outcome(variant, suite, compared) for variant, suite in evaluated]

    variants: list[AblationVariant] = []
    solo: dict[ConfigDimension, Fraction] = {}
    for outcome in (o for o in outcomes if not o.variant.is_pair):
        dimension = outcome.variant.dimensions[0]
        if outcome.variant.direction is FORWARD:
            effect = _pp(outcome.passed - pa, n)
            gained, lost = _flips(a_passed, outcome.verdicts)
            solo[dimension] = effect
        else:
            # What d contributes inside B: cases B passes only with d are gained.
            effect = _pp(pb - outcome.passed, n)
            lost, gained = _flips(b_passed, outcome.verdicts)
        variants.append(
            AblationVariant(
                dimension=dimension,
                direction=outcome.variant.direction,
                paths=list(outcome.variant.paths),
                agent_ref=outcome.suite.agent_config.ref,
                config_hash=outcome.suite.config_hash,
                suite_id=outcome.suite.suite_id,
                passed=outcome.passed,
                total=n,
                harness_errors=outcome.unjudged,
                effect_pp=float(effect),
                gained=gained,
                lost=lost,
            )
        )

    pairs: list[AblationPair] = []
    for outcome in (o for o in outcomes if o.variant.is_pair):
        first, second = outcome.variant.dimensions
        if first not in solo or second not in solo:
            raise ValueError(f"pair {outcome.variant.key} needs both forward variants")
        effect = _pp(outcome.passed - pa, n)
        gained, lost = _flips(a_passed, outcome.verdicts)
        pairs.append(
            AblationPair(
                dimensions=[first, second],
                agent_ref=outcome.suite.agent_config.ref,
                config_hash=outcome.suite.config_hash,
                suite_id=outcome.suite.suite_id,
                passed=outcome.passed,
                total=n,
                harness_errors=outcome.unjudged,
                effect_pp=float(effect),
                interaction_pp=float(effect - solo[first] - solo[second]),
                gained=gained,
                lost=lost,
            )
        )

    ablated = {variant.dimension for variant in variants}
    dimensions = [d for d in ablatable_dimensions(a.agent_config, b.agent_config) if d in ablated]
    total = _pp(pb - pa, n)
    solo_sum = sum(solo.values(), Fraction(0))
    diffs, diff_warnings = suite_diff(a, b)
    fields = {path for outcome in outcomes for path in outcome.variant.paths}
    suites = [a, b, *(outcome.suite for outcome in outcomes)]
    deterministic = all(suite.environment.deterministic for suite in suites)
    return Ablation(
        ablation_id=ablation_id or str(uuid.uuid4()),
        name=name,
        created_at=created_at or utcnow(),
        experiment_id=experiment_id,
        dataset_name=a.dataset_name,
        dataset_version=a.dataset_version,
        dataset_hash=a.dataset_hash,
        deterministic=deterministic,
        a=_summary(a),
        b=_summary(b),
        dimensions=dimensions,
        not_ablated=[d for d in diffs if d.path.split(".")[0] not in fields],
        variants=variants,
        pairs=pairs,
        pairs_skipped=pairs_skipped,
        total_effect_pp=float(total),
        sum_of_solo_effects_pp=float(solo_sum),
        interaction_pp=float(total - solo_sum),
        n=n,
        cases=_attributions(a_cases, b_cases, compared, dimensions, outcomes),
        warnings=[
            *diff_warnings,
            *warnings,
            *_caveats(a, b, len(shared), n, outcomes, deterministic=deterministic),
        ],
    )


def _attributions(
    a_cases: Mapping[str, CaseResult],
    b_cases: Mapping[str, CaseResult],
    compared: Mapping[str, CaseResult],
    dimensions: Sequence[ConfigDimension],
    outcomes: Sequence[_Outcome],
) -> list[CaseAttribution]:
    pairs = [o for o in outcomes if o.variant.is_pair]
    solo = [o for o in outcomes if not o.variant.is_pair]
    forward = {o.variant.dimensions[0]: o for o in solo if o.variant.direction is FORWARD}
    backward = {o.variant.dimensions[0]: o for o in solo if o.variant.direction is BACKWARD}
    attributions = []
    for case_id in compared:
        a_case, b_case = a_cases[case_id], b_cases[case_id]
        change = classify(a_case, b_case)
        if change not in _FLIPS:
            continue
        sufficient = [
            d for d in dimensions if d in forward and forward[d].verdicts[case_id] == b_case.passed
        ]
        necessary = [
            d
            for d in dimensions
            if d in backward and backward[d].verdicts[case_id] == a_case.passed
        ]
        runs = {"a": a_case.run_id, "b": b_case.run_id}
        runs.update({o.variant.key: o.runs.get(case_id) for o in outcomes})
        attributions.append(
            CaseAttribution(
                case_id=case_id,
                change=change,
                sufficient=sufficient,
                necessary=necessary,
                needs_combination=not sufficient,
                sufficient_pairs=[]
                if sufficient
                else [
                    list(o.variant.dimensions)
                    for o in pairs
                    if o.verdicts[case_id] == b_case.passed
                ],
                runs={key: run for key, run in runs.items() if run is not None},
            )
        )
    return attributions


def _caveats(
    a: SuiteResult,
    b: SuiteResult,
    shared: int,
    n: int,
    outcomes: Sequence[_Outcome],
    *,
    deterministic: bool,
) -> list[str]:
    """What limits how far the numbers can be trusted, beyond suite_diff's warnings."""
    caveats = []
    if n < shared:
        caveats.append(
            f"{shared - n} of {shared} shared cases are not compared: their content changed, "
            "or AgentScope could not judge them on A or B"
        )
    for outcome in outcomes:
        if outcome.unjudged:
            caveats.append(
                f"{outcome.suite.agent_config.ref}: {outcome.unjudged} of {n} compared cases "
                "could not be judged (harness or provider errors); they count as not passed "
                "and are not attributed"
            )
    drift = {
        difference.path
        for outcome in outcomes
        for side in (a, b)
        for difference in environment_diff(side, outcome.suite)
    }
    engines = {suite.environment.check_engine_version for suite in (a, b)}
    if any(o.suite.environment.check_engine_version not in engines for o in outcomes):
        drift.add("environment.check_engine_version")
    if drift:
        caveats.append(
            f"variants ran in another environment than A or B ({', '.join(sorted(drift))} "
            "differ): effects may include that change, not only the ablated dimension"
        )
    if not deterministic:
        caveats.append(
            "not every suite ran on the deterministic fake model: effects may be noise rather "
            "than the ablated dimension"
        )
    return caveats
