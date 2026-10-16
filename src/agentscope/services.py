"""Wires the runtime, storage, and evaluation together. Shared by the CLI and the API so
both use exactly the same engine."""

from __future__ import annotations

from dataclasses import dataclass

from agentscope.agents.base import AgentCatalog, default_catalog
from agentscope.config import Settings, get_settings
from agentscope.domain.datasets import EvalCase
from agentscope.domain.evaluation import CaseResult
from agentscope.domain.traces import TraceRecord
from agentscope.evaluation.engine import evaluate_case
from agentscope.providers.registry import build_providers
from agentscope.runtime.runner import AgentRunner, RunRequest
from agentscope.storage.db import create_db_engine, upgrade
from agentscope.storage.repository import Repository
from agentscope.tools.registry import ToolRegistry, default_registry
from agentscope.tracing.redaction import Redactor


def build_redactor(settings: Settings) -> Redactor:
    secrets = [s for s in (settings.openai_compatible_api_key,) if s]
    return Redactor(extra_keys=settings.redact_keys, secret_values=secrets)


@dataclass
class Services:
    settings: Settings
    catalog: AgentCatalog
    tools: ToolRegistry
    runner: AgentRunner
    repository: Repository
    redactor: Redactor

    async def run_case(
        self,
        request: RunRequest,
        case: EvalCase | None = None,
        *,
        suite_id: str | None = None,
        save: bool = True,
    ) -> tuple[TraceRecord, CaseResult | None]:
        """Run, evaluate against `case` when given, and persist both."""
        trace = await self.runner.run(request)
        evaluation = None
        if case is not None:
            schema = self.catalog.get(request.config.agent).output_schema()
            evaluation = evaluate_case(case, trace, schema)
        if save:
            self.repository.save_trace(trace, suite_id=suite_id, case=case, evaluation=evaluation)
        return trace, evaluation


def build_services(settings: Settings | None = None, *, migrate: bool = True) -> Services:
    settings = settings or get_settings()
    catalog = default_catalog()
    tools = default_registry()
    redactor = build_redactor(settings)
    engine = create_db_engine(settings.effective_database_url)
    if migrate:
        upgrade(engine)
    runner = AgentRunner(
        catalog,
        tools,
        build_providers(settings, catalog),
        redactor=redactor,
        model_rates=settings.model_rates,
    )
    return Services(
        settings=settings,
        catalog=catalog,
        tools=tools,
        runner=runner,
        repository=Repository(engine, redactor),
        redactor=redactor,
    )
