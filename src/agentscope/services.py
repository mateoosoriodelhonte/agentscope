"""Wires the runtime, storage, and evaluation together. Shared by the CLI and the API so
both use exactly the same engine.

Checks always judge the unredacted in-memory trace: a secret-shaped string the model leaked
is still a leak. The repository redacts everything it stores, and whatever this module hands
back for display (the CLI prints it, the API returns it) is a redacted copy.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, SecretStr
from sqlalchemy.engine import make_url

from agentscope import __version__
from agentscope.agents.base import AgentCatalog, default_catalog
from agentscope.api.schemas import (
    AgentInfo,
    AgentsResponse,
    DatasetSummary,
    DatasetValidation,
    ProviderStatus,
    SettingsView,
    ValidationIssue,
)
from agentscope.config import Settings, get_settings
from agentscope.datasets.issues import DatasetError, Issue, json_pointer
from agentscope.datasets.lint import lint
from agentscope.datasets.packaged import UnknownDatasetError, builtin_datasets
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
from agentscope.domain.experiments import Comparison
from agentscope.domain.traces import TraceRecord
from agentscope.evaluation.baseline import Baseline, ensure_compatible
from agentscope.evaluation.checks import CHECKS
from agentscope.evaluation.engine import evaluate_case
from agentscope.evaluation.fingerprint import environment_fingerprint
from agentscope.experiments.compare import compare
from agentscope.providers.base import ModelProvider
from agentscope.providers.http import EgressPolicy
from agentscope.providers.registry import build_providers
from agentscope.providers.status import provider_statuses
from agentscope.runtime.runner import AgentRunner, RunRequest
from agentscope.storage.db import create_db_engine, upgrade
from agentscope.storage.repository import DatasetConflict, Repository, SuiteHeader
from agentscope.suite_runner import SuiteRunner
from agentscope.tools.registry import ToolRegistry, default_registry
from agentscope.tracing.redaction import Redactor

_log = logging.getLogger(__name__)

# Called with (cases done, cases in total) after each case.
Progress = Callable[[int, int], None]

REDACTION_REFUSAL = (
    "looks like a secret and would be redacted when stored, silently changing the dataset; "
    "datasets must contain synthetic canaries (e.g. AS-CANARY-...), not secret-shaped values"
)
_MAX_ISSUES = 50


class UnknownSuiteError(LookupError):
    pass


def build_redactor(settings: Settings) -> Redactor:
    """Redacts the exact value of every secret in Settings (and the database password)."""
    secrets = [value.get_secret_value() for _, value in settings if isinstance(value, SecretStr)]
    password = make_url(settings.effective_database_url).password
    if password:
        secrets.append(str(password))
    return Redactor(extra_keys=settings.redact_keys, secret_values=secrets)


def egress_destinations(settings: Settings) -> list[str]:
    """Every network destination Settings allow, plus configured ones the policy refuses."""
    policy = EgressPolicy.from_settings(settings)
    allowed = [f"{name}: {target.url('')}" for name, target in sorted(policy.targets.items())]
    refused = [f"{name}: refused ({reason})" for name, reason in sorted(policy.refusals.items())]
    return allowed + refused


# Settings holding a URL, shown in /api/settings with any credentials masked.
_URL_SETTINGS = ("ollama_url", "openai_compatible_url", "otlp_endpoint")


def display_url(raw: str) -> str:
    """A configured URL safe to show: user info masked, query and fragment dropped. Plain
    string handling, so even a malformed value (no scheme, bad port) never leaks them."""
    scheme, separator, rest = raw.partition("://")
    if not separator:
        scheme, rest = "", raw
    end = min((i for i in map(rest.find, "/?#") if i >= 0), default=len(rest))
    authority, path = rest[:end], rest[end:]
    if "@" in authority:
        authority = "***@" + authority.rsplit("@", 1)[1]
    path = path.split("?", 1)[0].split("#", 1)[0]
    return f"{scheme}{separator}{authority}{path}"


def _otlp_statement(settings: Settings) -> str:
    """What happens to traces, from the egress policy (never the raw configured URL)."""
    if not settings.otlp_endpoint:
        return "Traces are not exported anywhere (no OTLP endpoint is configured)."
    policy = EgressPolicy.from_settings(settings)
    target = policy.targets.get("otlp")
    if target is None:
        reason = policy.refusals.get("otlp", "not allowed")
        return f"An OTLP endpoint is configured but refused, so no traces go there ({reason})."
    return (
        f"An OTLP endpoint is configured: trace export may send span data to {target.address}, "
        "outside this database."
    )


def validation_issue(issue: Issue) -> ValidationIssue:
    path = f"{issue.source}#{issue.path}" if issue.source else issue.path
    return ValidationIssue(path=path, message=issue.message, severity=issue.severity)


def validation_report(dataset: Dataset | None, issues: Sequence[Issue]) -> DatasetValidation:
    return DatasetValidation(
        valid=dataset is not None and all(i.severity != "error" for i in issues),
        issues=[validation_issue(i) for i in issues],
        content_hash=dataset.content_hash() if dataset is not None else None,
        case_count=len(dataset.cases) if dataset is not None else 0,
    )


def _changed_paths(before: Any, after: Any, path: tuple[str | int, ...] = ()) -> Iterator[str]:
    """JSON pointers where redaction changed a value (it keeps keys and list lengths)."""
    if isinstance(before, dict) and isinstance(after, dict):
        for key, value in before.items():
            yield from _changed_paths(value, after.get(str(key)), (*path, key))
    elif isinstance(before, list) and isinstance(after, list) and len(before) == len(after):
        for index, (left, right) in enumerate(zip(before, after, strict=True)):
            yield from _changed_paths(left, right, (*path, index))
    elif before != after:
        yield json_pointer(path)


def _describe(exc: BaseException) -> str:
    if isinstance(exc, asyncio.CancelledError | KeyboardInterrupt):
        return "cancelled: AgentScope stopped before the evaluation finished"
    message = str(exc)
    return f"{type(exc).__name__}: {message}" if message else type(exc).__name__


@dataclass
class Services:
    settings: Settings
    catalog: AgentCatalog
    tools: ToolRegistry
    providers: dict[str, ModelProvider]
    runner: AgentRunner
    repository: Repository
    redactor: Redactor

    def redacted[M: BaseModel](self, model: M) -> M:
        """A copy safe to print or return: exactly what the repository would store."""
        dumped = model.model_dump(mode="json", exclude_unset=True)
        return type(model).model_validate(self.redactor.redact(dumped))

    # --- agents, providers, settings ----------------------------------------------------

    def agents(self) -> AgentsResponse:
        return AgentsResponse(
            agents=[
                AgentInfo(
                    name=d.name,
                    title=d.title,
                    description=d.description,
                    toolset=list(d.toolset),
                    output_schema=d.output_schema(),
                    prompts=dict(d.prompts),
                    versions=list(d.versions),
                )
                for d in self.catalog.definitions()
            ],
            tools=self.tools.all_specs(),
        )

    def resolve_agent(
        self, agent_ref: str | None = None, config: AgentConfig | None = None
    ) -> AgentConfig:
        """A built-in version or an explicit config, checked against the catalog and the
        enabled providers. Raises ValueError, UnknownAgentError or LookupError."""
        if (agent_ref is None) == (config is None):
            raise ValueError("provide exactly one of agent_ref or config")
        resolved = config if config is not None else self.catalog.resolve(agent_ref or "")
        self.catalog.validate(resolved)
        self.runner.provider(resolved.model.provider.value)
        return resolved

    async def provider_statuses(self) -> list[ProviderStatus]:
        return await provider_statuses(self.settings, self.providers)

    def settings_view(self) -> SettingsView:
        storage = self.repository.inventory()
        # SecretStr fields dump as "**********": keys, the API token and the DSN. Configured
        # URLs are plain strings, so credentials or a query someone embedded in one are
        # masked too (the egress policy refuses such URLs, but would not hide them here).
        effective = self.settings.model_dump(mode="json")
        for key in _URL_SETTINGS:
            if isinstance(effective.get(key), str):
                effective[key] = self.redactor.redact_text(display_url(effective[key]))
        return SettingsView(
            version=__version__,
            effective=effective,
            storage=storage,
            egress=egress_destinations(self.settings),
            privacy=[
                f"Everything AgentScope records stays in its {storage.backend} database at "
                f"{storage.location}. It sends no telemetry, analytics or crash reports.",
                "Stored: agent runs (task text, final outputs, tool calls with their arguments "
                "and results), trace spans (prompts and model responses), imported datasets, "
                "evaluation verdicts and comparisons.",
                "Secrets are redacted before anything is stored: configured keys and tokens, "
                "values under sensitive key names, and secret-shaped strings such as API keys, "
                "bearer tokens, JWTs and private keys.",
                "Not stored: provider API keys, the API token and the database password. They "
                "are masked here.",
                "Model calls go only to the destinations listed under egress, and only when a "
                "run selects that provider (or its status is checked).",
                _otlp_statement(self.settings),
                "Token usage is shown only when a provider reports it, never estimated. Cost is "
                "shown only as an estimate from rates you configure.",
                "Purging deletes the selected rows and compacts the database, so their contents "
                "are gone from disk, not just unlinked.",
            ],
        )

    # --- runs ---------------------------------------------------------------------------

    async def run_case(
        self, request: RunRequest, case: EvalCase | None = None, *, save: bool = True
    ) -> tuple[TraceRecord, CaseResult | None]:
        """Run, evaluate the unredacted trace against `case` when given, persist both (the
        repository redacts them), and return redacted copies."""
        trace = await self.runner.run(request)
        evaluation = None
        if case is not None:
            schema = self.catalog.get(request.config.agent).output_schema()
            evaluation = evaluate_case(case, trace, schema)
        if save:
            await asyncio.to_thread(
                self.repository.save_trace, trace, case=case, evaluation=evaluation
            )
        return self.redacted(trace), self.redacted(evaluation) if evaluation else None

    # --- datasets -----------------------------------------------------------------------

    def validate_dataset(
        self, dataset: Dataset, *, config: AgentConfig | None = None
    ) -> list[Issue]:
        """Lint issues, plus an error for every value redaction would rewrite on save."""
        before = dataset.model_dump(mode="json")
        changed = list(_changed_paths(before, self.redactor.redact(before)))
        refusals = [Issue(pointer, REDACTION_REFUSAL) for pointer in changed[:_MAX_ISSUES]]
        return [*lint(dataset, self.catalog, self.tools, config=config), *refusals]

    def import_dataset(self, dataset: Dataset) -> DatasetSummary:
        """Store a dataset exactly as given. Raises DatasetError (invalid, or it would change
        on save) or DatasetConflict (name@version exists with other content)."""
        errors = [i for i in self.validate_dataset(dataset) if i.severity == "error"]
        if errors:
            raise DatasetError(errors)
        return self.repository.save_dataset(dataset)

    def stored_dataset(self, name: str, version: str | None = None) -> Dataset:
        """A stored dataset version, or its most recently imported one."""
        detail = (
            self.repository.get_dataset(name, version)
            if version
            else self.repository.latest_dataset(name)
        )
        if detail is None:
            ref = f"{name}@{version}" if version else name
            raise UnknownDatasetError(f"no stored dataset {ref}")
        return detail.dataset

    def register_builtin_datasets(self) -> None:
        """Idempotent. A stored copy that differs (an older release) is kept, with a warning."""
        for dataset in builtin_datasets():
            try:
                self.repository.save_dataset(dataset)
            except DatasetConflict as exc:
                _log.warning("built-in dataset not registered: %s", exc)

    # --- evaluations --------------------------------------------------------------------

    def check_suite_inputs(
        self,
        dataset: Dataset,
        config: AgentConfig,
        *,
        checks: Sequence[CheckName] | None = None,
        baseline: Baseline | None = None,
    ) -> list[CheckName]:
        """Everything that can be wrong before a suite starts, raised before anything is run
        or stored. Returns the enabled checks."""
        self.catalog.validate(config)
        self.runner.provider(config.model.provider.value)
        errors = [
            i
            for i in lint(dataset, self.catalog, self.tools, config=config)
            if i.severity == "error"
        ]
        if errors:
            raise DatasetError(errors)
        enabled = sorted(set(CHECKS) if checks is None else set(checks))
        if not enabled:
            raise ValueError("at least one check must be enabled")
        if baseline is not None:
            ensure_compatible(
                baseline,
                dataset_hash=dataset.content_hash(),
                checks_enabled=enabled,
                check_engine_version=CHECK_ENGINE_VERSION,
            )
        return enabled

    async def run_suite(
        self,
        dataset: Dataset,
        config: AgentConfig,
        *,
        label: str = "",
        checks: Sequence[CheckName] | None = None,
        thresholds: GateThresholds | None = None,
        baseline: Baseline | None = None,
        progress: Progress | None = None,
        suite_id: str | None = None,
        save: bool = True,
    ) -> SuiteResult:
        """Evaluate `dataset` on `config` and return the redacted result.

        When saved, the suite exists (as `running`) before its first case, every case is
        stored as soon as it is judged, and a suite that cannot finish (cancelled, crashed)
        is marked as errored, so a partial suite is never mistaken for a complete one.
        """
        enabled = self.check_suite_inputs(dataset, config, checks=checks, baseline=baseline)
        suite_id = suite_id or str(uuid.uuid4())
        total = len(dataset.cases)
        done = 0

        async def on_case(case: EvalCase, trace: TraceRecord | None, result: CaseResult) -> None:
            nonlocal done
            if save:
                await asyncio.to_thread(self._persist_case, suite_id, case, trace, result)
            done += 1
            if progress is not None:
                progress(done, total)

        if save:
            header = SuiteHeader(
                suite_id=suite_id,
                label=label,
                dataset_name=dataset.name,
                dataset_version=dataset.version,
                dataset_hash=dataset.content_hash(),
                agent_config=config,
                config_hash=config.config_hash(),
                effective_config_hash=self.runner.effective_config_hash(config),
                checks_enabled=enabled,
                environment=environment_fingerprint(config),
                started_at=utcnow(),
            )
            await asyncio.to_thread(self.repository.create_suite, header)
        try:
            result = await SuiteRunner(self.runner, self.catalog).run(
                dataset,
                config,
                suite_id=suite_id,
                label=label,
                checks=enabled,
                thresholds=thresholds,
                baseline=baseline,
                on_case=on_case,
            )
            if save:
                await asyncio.to_thread(self.repository.finish_suite, result)
        except BaseException as exc:
            if save:
                self._fail_suite(suite_id, exc)
            raise
        return self.redacted(result)

    def _persist_case(
        self, suite_id: str, case: EvalCase, trace: TraceRecord | None, result: CaseResult
    ) -> None:
        if trace is not None:
            self.repository.save_trace(trace, suite_id=suite_id, case=case, evaluation=result)
        self.repository.record_case(suite_id, result)

    def _fail_suite(self, suite_id: str, exc: BaseException) -> None:
        # Synchronous on purpose: it must complete even while the task is being cancelled.
        try:
            self.repository.fail_suite(suite_id, _describe(exc))
        except Exception:
            # Startup marks suites left running as interrupted, so nothing is lost for good.
            _log.warning("could not mark suite %s as failed", suite_id)

    # --- experiments --------------------------------------------------------------------

    async def run_experiment(
        self,
        name: str,
        dataset: Dataset,
        a_config: AgentConfig,
        b_config: AgentConfig,
        *,
        experiment_id: str | None = None,
        progress: Progress | None = None,
    ) -> Comparison:
        """Evaluate both configurations on `dataset` (A first), then compare and store."""
        self.check_suite_inputs(dataset, a_config)
        self.check_suite_inputs(dataset, b_config)
        total = 2 * len(dataset.cases)

        def report(offset: int) -> Progress | None:
            callback = progress
            if callback is None:
                return None
            return lambda done, _total: callback(offset + done, total)

        a = await self.run_suite(dataset, a_config, label=f"{name} (A)", progress=report(0))
        b = await self.run_suite(
            dataset, b_config, label=f"{name} (B)", progress=report(len(dataset.cases))
        )
        return await asyncio.to_thread(self._save_comparison, name, a, b, experiment_id)

    def compare_suites(self, name: str, suite_a: str, suite_b: str) -> Comparison:
        """Compare two stored, completed suites (A is the reference) and store the result."""
        suites = []
        for suite_id in (suite_a, suite_b):
            suite = self.repository.get_suite(suite_id)
            if suite is None:
                raise UnknownSuiteError(f"evaluation {suite_id} not found")
            if suite.status != "completed":
                raise ValueError(
                    f"evaluation {suite_id} is {suite.status}; only completed ones can be compared"
                )
            suites.append(suite)
        return self._save_comparison(name, suites[0], suites[1])

    def _save_comparison(
        self, name: str, a: SuiteResult, b: SuiteResult, experiment_id: str | None = None
    ) -> Comparison:
        comparison = compare(a, b, name, experiment_id=experiment_id)
        self.repository.save_experiment(comparison)
        return self.redacted(comparison)


def build_services(settings: Settings | None = None, *, migrate: bool = True) -> Services:
    """With `migrate`: bring the schema to head, turn work a crashed process left `running`
    into errors, and register the packaged datasets."""
    settings = settings or get_settings()
    catalog = default_catalog()
    tools = default_registry()
    redactor = build_redactor(settings)
    engine = create_db_engine(settings.effective_database_url)
    providers = build_providers(settings, catalog)
    runner = AgentRunner(
        catalog, tools, providers, redactor=redactor, model_rates=settings.model_rates
    )
    services = Services(
        settings=settings,
        catalog=catalog,
        tools=tools,
        providers=providers,
        runner=runner,
        repository=Repository(engine, redactor),
        redactor=redactor,
    )
    if migrate:
        upgrade(engine)
        services.repository.mark_interrupted()
        services.register_builtin_datasets()
    return services
