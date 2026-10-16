"""Canonical baselines: the reviewable record a regression gate compares against.

A baseline holds only what is deterministic for a deterministic provider: no ids,
timestamps or durations. It is written as canonical JSON, so regenerating it from an
unchanged agent and dataset yields the same bytes, and any change shows up as a diff.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Literal

from pydantic import ValidationError

from agentscope.domain.common import StrictModel, canonical_json
from agentscope.domain.evaluation import CheckName, CheckStatus, SuiteMetrics, SuiteResult


class BaselineError(ValueError):
    """A baseline that cannot be used: unreadable, invalid, or for a different suite."""


class IncompatibleBaselineError(BaselineError):
    """The baseline was recorded on other dataset content or another check engine."""


class BaselineCase(StrictModel):
    case_id: str
    case_hash: str
    passed: bool
    failed_checks: list[CheckName]


class Baseline(StrictModel):
    schema_version: Literal[1] = 1
    dataset_hash: str
    effective_config_hash: str
    check_engine_version: str
    checks_enabled: list[CheckName]
    metrics: SuiteMetrics  # without the informational timings
    cases: list[BaselineCase]


def build_baseline(result: SuiteResult) -> Baseline:
    return Baseline(
        dataset_hash=result.dataset_hash,
        effective_config_hash=result.effective_config_hash,
        check_engine_version=result.environment.check_engine_version,
        checks_enabled=sorted(result.checks_enabled),
        metrics=result.metrics.model_copy(update={"timings": None}),
        cases=[
            BaselineCase(
                case_id=c.case_id,
                case_hash=c.case_hash,
                passed=c.passed,
                failed_checks=[r.name for r in c.checks if r.status is CheckStatus.FAILED],
            )
            for c in sorted(result.cases, key=lambda c: c.case_id)
        ],
    )


def baseline_json(baseline: Baseline) -> str:
    return canonical_json(baseline.model_dump(mode="json")) + "\n"


def write_baseline(baseline: Baseline, path: Path) -> None:
    path.write_text(baseline_json(baseline), encoding="utf-8")


def read_baseline(path: Path) -> Baseline:
    try:
        return Baseline.model_validate_json(path.read_bytes())
    except OSError as exc:
        raise BaselineError(f"cannot read baseline {path}: {exc.strerror}") from None
    except ValidationError as exc:
        raise BaselineError(f"invalid baseline {path}: {exc.error_count()} error(s)") from None


def _major(version: str) -> str:
    return version.split(".", 1)[0]


def ensure_compatible(
    baseline: Baseline,
    *,
    dataset_hash: str,
    checks_enabled: Iterable[CheckName],
    check_engine_version: str,
) -> None:
    """A baseline is comparable only for the same dataset content, the same enabled
    checks, and a check engine with the same major version."""
    problems = []
    if baseline.dataset_hash != dataset_hash:
        problems.append(f"dataset hash {baseline.dataset_hash} differs from {dataset_hash}")
    if sorted(baseline.checks_enabled) != sorted(checks_enabled):
        problems.append("enabled checks differ")
    if _major(baseline.check_engine_version) != _major(check_engine_version):
        problems.append(f"check engine {baseline.check_engine_version} vs {check_engine_version}")
    if problems:
        raise IncompatibleBaselineError("baseline is not comparable: " + "; ".join(problems))
