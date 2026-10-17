"""Runs a dataset against one agent configuration and aggregates the verdicts.

Cases run sequentially (deterministic order, one model at a time). A harness failure in
one case becomes that case's `harness_error` instead of aborting the suite. Persistence
and progress reporting belong to the caller, through the `on_case` hook; this module never
touches storage or the API.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable, Iterable
from typing import Any

from agentscope.agents.base import AgentCatalog
from agentscope.domain.agents import AgentConfig
from agentscope.domain.common import utcnow
from agentscope.domain.datasets import Dataset, EvalCase
from agentscope.domain.evaluation import (
    CHECK_ENGINE_VERSION,
    CaseResult,
    CheckName,
    GateThresholds,
    SuiteResult,
)
from agentscope.domain.traces import TokenUsage, TraceRecord
from agentscope.evaluation.baseline import Baseline, ensure_compatible
from agentscope.evaluation.checks import CHECKS
from agentscope.evaluation.engine import evaluate_case
from agentscope.evaluation.fingerprint import environment_fingerprint
from agentscope.evaluation.gates import evaluate_gate
from agentscope.evaluation.metrics import compute_metrics
from agentscope.runtime.runner import AgentRunner, RunRequest

# Called after each case with its trace (None when the run itself failed) and verdict.
OnCase = Callable[[EvalCase, TraceRecord | None, CaseResult], Awaitable[None]]

# Error codes the runtime records when AgentScope itself failed during a run (it catches
# its own exceptions and returns a trace, so they never reach the try/except below), or when
# the model provider failed (unreachable, timed out, an error response): the agent never got
# to act, so these are harness or infrastructure errors, not agent failures. They are kept
# out of verdicts and per-check rates, fail the gate as harness errors (exit 3), and are
# never written into a baseline.
INTERNAL_ERROR_CODES = frozenset({"internal_error", "internal_recursion_limit"})
PROVIDER_ERROR_CODES = frozenset({"provider_error", "provider_timeout"})
HARNESS_ERROR_CODES = INTERNAL_ERROR_CODES | PROVIDER_ERROR_CODES


class IncompatibleDatasetError(ValueError):
    """The dataset (or some of its cases) targets a different agent."""


def harness_error(case: EvalCase, trace: TraceRecord | None, description: str) -> CaseResult:
    """A failure of AgentScope or the provider, not of the agent: never counted as a pass,
    and not judged by any check (so per-check rates measure only the agent)."""
    return CaseResult(
        case_id=case.id,
        case_hash=case.case_hash(),
        run_id=trace.run_id if trace else None,
        trace_id=trace.trace_id if trace else None,
        passed=False,
        status=trace.status if trace else None,
        harness_error=description[:300],
        steps=trace.steps if trace else 0,
        duration_ms=trace.duration_ms if trace else 0,
        tags=list(case.tags),
    )


def judge_case(
    case: EvalCase,
    trace: TraceRecord,
    output_schema: dict[str, Any],
    checks: Iterable[CheckName] | None = None,
) -> CaseResult:
    """The verdict on one finished run, or a harness error when the harness or the provider
    broke the run (see HARNESS_ERROR_CODES)."""
    if trace.error_code in HARNESS_ERROR_CODES:
        return harness_error(case, trace, f"{trace.error_code}: {trace.error or 'no detail'}")
    return evaluate_case(case, trace, output_schema, checks)


def run_request(case: EvalCase, config: AgentConfig) -> RunRequest:
    return RunRequest(
        config=config,
        task=case.task,
        faults=list(case.faults),
        model_faults=list(case.model_faults),
        permitted_tools=case.permitted_tools,
        forbidden_tools=list(case.forbidden_tools),
        limits=case.limits,
        case_id=case.id,
    )


class SuiteRunner:
    def __init__(self, runner: AgentRunner, catalog: AgentCatalog) -> None:
        self._runner = runner
        self._catalog = catalog

    async def run(
        self,
        dataset: Dataset,
        config: AgentConfig,
        *,
        suite_id: str | None = None,
        label: str = "",
        checks: Iterable[CheckName] | None = None,
        thresholds: GateThresholds | None = None,
        baseline: Baseline | None = None,
        on_case: OnCase | None = None,
    ) -> SuiteResult:
        """Input problems (wrong agent, unknown config, disabled provider, no enabled
        checks, incomparable baseline) raise before any case runs. Exceptions raised by
        `on_case` propagate: the caller owns them."""
        self._catalog.validate(config)
        self._runner.provider(config.model.provider.value)
        self._check_agent(dataset, config)
        enabled = sorted(set(CHECKS) if checks is None else set(checks))
        if not enabled:
            # With nothing to judge, every case would pass.
            raise ValueError("at least one check must be enabled")
        dataset_hash = dataset.content_hash()
        if baseline is not None:
            ensure_compatible(
                baseline,
                dataset_hash=dataset_hash,
                checks_enabled=enabled,
                check_engine_version=CHECK_ENGINE_VERSION,
            )
        output_schema = self._catalog.get(config.agent).output_schema()
        started = utcnow()
        results: list[CaseResult] = []
        usage: dict[str, TokenUsage | None] = {}
        for case in sorted(dataset.cases, key=lambda c: c.id):
            trace: TraceRecord | None = None
            try:
                trace = await self._runner.run(run_request(case, config))
                result = judge_case(case, trace, output_schema, enabled)
            except Exception as exc:
                message = self._runner.redactor.redact_text(str(exc))
                description = f"{type(exc).__name__}: {message}" if message else type(exc).__name__
                result = harness_error(case, trace, description)
            if trace is not None:
                usage[trace.run_id] = trace.usage
            results.append(result)
            if on_case is not None:
                await on_case(case, trace, result)

        suite = SuiteResult(
            suite_id=suite_id or str(uuid.uuid4()),
            label=label,
            dataset_name=dataset.name,
            dataset_version=dataset.version,
            dataset_hash=dataset_hash,
            agent_config=config,
            config_hash=config.config_hash(),
            effective_config_hash=self._runner.effective_config_hash(config),
            checks_enabled=enabled,
            environment=environment_fingerprint(config),
            started_at=started,
            finished_at=utcnow(),
            cases=results,
            metrics=compute_metrics(results, usage),
        )
        if thresholds is None:
            return suite
        return suite.model_copy(update={"gate": evaluate_gate(thresholds, suite, baseline)})

    @staticmethod
    def _check_agent(dataset: Dataset, config: AgentConfig) -> None:
        problems = []
        if dataset.agent not in (None, config.agent):
            problems.append(f"the dataset targets {dataset.agent!r}")
        wrong = [c.id for c in dataset.cases if c.agent not in (None, config.agent)]
        if wrong:
            problems.append(f"cases target another agent: {', '.join(wrong[:10])}")
        if problems:
            raise IncompatibleDatasetError(
                f"cannot run dataset {dataset.name!r} on {config.agent!r}: " + "; ".join(problems)
            )
