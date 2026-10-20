"""The single persistence choke point. Every write is scrubbed by the redactor first."""

from __future__ import annotations

import uuid
from collections import Counter
from collections.abc import Callable, Collection, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ValidationError
from sqlalchemy import (
    Connection,
    Engine,
    Integer,
    Select,
    cast,
    delete,
    func,
    select,
    text,
    update,
)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import InstrumentedAttribute, Session, defer, selectinload, sessionmaker

from agentscope.api.schemas import (
    DatasetDetail,
    DatasetSummary,
    ExperimentListItem,
    PurgeResult,
    StorageInventory,
    SuiteListItem,
    TableInventory,
)
from agentscope.domain.ablations import Ablation, AblationListItem
from agentscope.domain.agents import AgentConfig
from agentscope.domain.common import StrictModel
from agentscope.domain.datasets import Dataset, EvalCase
from agentscope.domain.evaluation import (
    CaseResult,
    CheckName,
    CheckStats,
    CheckStatus,
    EnvironmentFingerprint,
    SuiteMetrics,
    SuiteResult,
)
from agentscope.domain.experiments import CaseChange, Comparison
from agentscope.domain.traces import (
    RunStatus,
    RunSummary,
    SpanEvent,
    SpanKind,
    SpanRecord,
    SpanStatus,
    TraceRecord,
)
from agentscope.storage.db import session_factory
from agentscope.storage.liveness import STALE_AFTER, owner_alive, process_owner
from agentscope.storage.models import (
    AblationRow,
    Base,
    CaseResultRow,
    CheckResultRow,
    DatasetRow,
    ExperimentRow,
    RunRow,
    SpanRow,
    SuiteRunRow,
)
from agentscope.tracing.redaction import Redactor

PurgeScope = Literal["all", "runs", "evaluations", "experiments", "datasets"]

INTERRUPTED = "interrupted: the process stopped before this finished"

# Ends a stored string that had to be cut back to its field's limit after redaction.
TRUNCATED = "…[truncated]"

# Every table holding user data, with what it contains in plain language.
_INVENTORY: tuple[tuple[type[Base], str], ...] = (
    (RunRow, "Agent runs: task text, final output, tool calls with their arguments, verdicts."),
    (SpanRow, "Trace spans: prompts, model responses, tool inputs and outputs."),
    (DatasetRow, "Imported evaluation datasets, including every test case."),
    (SuiteRunRow, "Evaluation runs: agent configuration, environment, metrics, gate results."),
    (CaseResultRow, "Per-case verdicts of evaluation runs, with check messages and evidence."),
    (CheckResultRow, "Per-check status and reason code of each case verdict."),
    (ExperimentRow, "Comparisons between two evaluation runs."),
    (AblationRow, "Ablations: which configuration changes explain a comparison, per case."),
)

# What each purge scope deletes, and the column `before` is compared with. Foreign keys
# cascade the rest: a suite takes its case results, its runs, and the experiments and
# ablations built on it. Purge itself deletes the variant suites of every deleted ablation.
_PURGE_TARGETS: dict[str, tuple[tuple[type[Base], InstrumentedAttribute[datetime]], ...]] = {
    "experiments": (
        (ExperimentRow, ExperimentRow.created_at),
        (AblationRow, AblationRow.created_at),
    ),
    "evaluations": ((SuiteRunRow, SuiteRunRow.started_at),),
    "runs": ((RunRow, RunRow.started_at),),
    "datasets": ((DatasetRow, DatasetRow.created_at),),
}

# JSON documents the suite list view never needs.
_SUITE_DOCUMENTS = (
    SuiteRunRow.agent_config,
    SuiteRunRow.checks_enabled,
    SuiteRunRow.environment,
    SuiteRunRow.metrics,
    SuiteRunRow.gate,
)


class DatasetConflict(Exception):
    """The name@version already exists with different content. Versions are immutable."""


class PurgeIncomplete(RuntimeError):
    """Rows were deleted, but their old contents could not be erased from disk yet.

    Another connection kept reading, so SQLite could not fold the write-ahead log back into
    the database and truncate it. Purging again once it is gone finishes the job.
    """

    def __init__(self, message: str, result: PurgeResult) -> None:
        super().__init__(message)
        self.result = result


class SuiteHeader(StrictModel):
    """What is known when a suite starts; `finish_suite` adds the metrics and the gate."""

    suite_id: str
    label: str = ""
    dataset_name: str
    dataset_version: str
    dataset_hash: str
    agent_config: AgentConfig
    config_hash: str
    effective_config_hash: str
    checks_enabled: list[CheckName]
    environment: EnvironmentFingerprint
    started_at: datetime


@dataclass(frozen=True, slots=True)
class EvaluatedCase:
    """A case verdict with what failure analysis groups it by."""

    suite_id: str
    agent_ref: str
    deterministic: bool
    result: CaseResult
    # Tool and model fault kinds injected into the case's run (empty once it is purged).
    fault_kinds: tuple[str, ...]


def scrubbed[M: BaseModel](model: M, redactor: Redactor) -> M:
    """A redacted copy, validated back into the same contract: what the repository stores.

    Only explicitly set fields go through the round trip, so validators that depend on
    which fields were given (an argument matcher's single comparison) still accept it.
    A redaction marker can be longer than the secret it replaces, pushing a string that was
    cut to its limit past it: such strings are cut again (the secret is already gone) rather
    than failing the save and, with it, a whole suite.
    """
    redacted = redactor.redact(model.model_dump(mode="json", exclude_unset=True))
    try:
        return type(model).model_validate(redacted)
    except ValidationError as exc:
        if not _cut_overlong_strings(redacted, exc):
            raise
        return type(model).model_validate(redacted)


def _ns(value: datetime) -> int:
    return int(value.timestamp() * 1_000_000) * 1000


def _from_ns(value: int) -> datetime:
    return datetime.fromtimestamp(value / 1e9, tz=UTC)


def _uuid(value: str) -> uuid.UUID | None:
    try:
        return uuid.UUID(value)
    except ValueError:
        return None


class Repository:
    def __init__(self, engine: Engine, redactor: Redactor | None = None) -> None:
        self.engine = engine
        self._sessions: sessionmaker[Session] = session_factory(engine)
        self._redactor = redactor or Redactor()

    def session(self) -> Session:
        return self._sessions()

    def _scrub[M: BaseModel](self, model: M) -> M:
        return scrubbed(model, self._redactor)

    # --- runs -------------------------------------------------------------------------

    def save_trace(
        self,
        trace: TraceRecord,
        *,
        suite_id: str | None = None,
        case: EvalCase | None = None,
        evaluation: CaseResult | None = None,
    ) -> None:
        """Persist a finished run. `suite_id` must name a suite created with create_suite."""
        clean = self._scrub(trace)
        clean_case = self._scrub(case) if case else None
        clean_eval = self._scrub(evaluation) if evaluation else None
        row = RunRow(
            id=uuid.UUID(clean.run_id),
            trace_id=clean.trace_id,
            agent=clean.agent,
            agent_version=clean.agent_version,
            config_hash=clean.config_hash,
            effective_config_hash=clean.effective_config_hash,
            provider=clean.provider,
            model=clean.model,
            status=clean.status.value,
            error_code=clean.error_code,
            error=clean.error,
            task=clean.task,
            case_id=clean.case_id,
            suite_id=uuid.UUID(suite_id) if suite_id else None,
            started_at=clean.started_at,
            ended_at=clean.ended_at,
            duration_ms=clean.duration_ms,
            steps=clean.steps,
            tool_calls=len(clean.tool_invocations),
            blocked_tool_calls=sum(1 for i in clean.tool_invocations if not i.executed),
            guardrail_count=len(clean.guardrail_events),
            fault_count=len(clean.faults) + len(clean.model_faults),
            config=clean.config.model_dump(mode="json"),
            limits=clean.limits.model_dump(mode="json"),
            permitted_tools=list(clean.permitted_tools),
            final_output=clean.final_output,
            usage=clean.usage.model_dump(mode="json") if clean.usage else None,
            cost=clean.cost.model_dump(mode="json") if clean.cost else None,
            faults=[f.model_dump(mode="json") for f in clean.faults],
            model_faults=[f.model_dump(mode="json") for f in clean.model_faults],
            transitions=[t.model_dump(mode="json") for t in clean.transitions],
            tool_invocations=[i.model_dump(mode="json") for i in clean.tool_invocations],
            guardrail_events=[g.model_dump(mode="json") for g in clean.guardrail_events],
            # Set fields only: a full dump gives an argument matcher two comparisons
            # (`"equals": null` next to the one in use) and could not be read back.
            eval_case=clean_case.model_dump(mode="json", exclude_unset=True)
            if clean_case
            else None,
            evaluation=clean_eval.model_dump(mode="json") if clean_eval else None,
            passed=clean_eval.passed if clean_eval else None,
            created_at=datetime.now(UTC),
        )
        if clean.status is RunStatus.RUNNING:
            # In flight: recovery must be able to tell whether its process still lives.
            row.owner, row.heartbeat_at = process_owner(), row.created_at
        row.spans = [
            SpanRow(
                trace_id=span.trace_id,
                span_id=span.span_id,
                seq=index,
                parent_span_id=span.parent_span_id,
                name=span.name[:256],
                kind=span.kind.value,
                status=span.status.value,
                status_message=span.status_message,
                start_time_unix_nano=_ns(span.start_time),
                end_time_unix_nano=_ns(span.end_time),
                duration_ms=span.duration_ms,
                attributes=dict(span.attributes),
                input=span.input,
                output=span.output,
                events=[e.model_dump(mode="json") for e in span.events],
            )
            for index, span in enumerate(clean.spans)
        ]
        with self.session() as session, session.begin():
            session.add(row)

    def get_run(self, run_id: str) -> tuple[TraceRecord, EvalCase | None, CaseResult | None] | None:
        """A trace plus the case it was evaluated against and the verdict, if any."""
        key = _uuid(run_id)
        if key is None:
            return None
        with self.session() as session:
            row = session.scalar(
                select(RunRow).where(RunRow.id == key).options(selectinload(RunRow.spans))
            )
            if row is None:
                return None
            case = EvalCase.model_validate(row.eval_case) if row.eval_case else None
            evaluation = CaseResult.model_validate(row.evaluation) if row.evaluation else None
            return _to_trace(row), case, evaluation

    def get_trace(self, run_id: str) -> TraceRecord | None:
        found = self.get_run(run_id)
        return found[0] if found else None

    def list_runs(
        self,
        *,
        agent: str | None = None,
        agent_version: str | None = None,
        status: str | None = None,
        has_faults: bool | None = None,
        has_blocked_calls: bool | None = None,
        verdict: Literal["passed", "failed", "none"] | None = None,
        case_id: str | None = None,
        suite_id: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[RunSummary], int]:
        """Newest first. `verdict="none"` selects runs that were never evaluated."""
        query = select(RunRow)
        if agent:
            query = query.where(RunRow.agent == agent)
        if agent_version:
            query = query.where(RunRow.agent_version == agent_version)
        if status:
            query = query.where(RunRow.status == status)
        if has_faults is not None:
            query = query.where(RunRow.fault_count > 0 if has_faults else RunRow.fault_count == 0)
        if has_blocked_calls is not None:
            blocked = RunRow.blocked_tool_calls
            query = query.where(blocked > 0 if has_blocked_calls else blocked == 0)
        if verdict == "none":
            query = query.where(RunRow.passed.is_(None))
        elif verdict is not None:
            query = query.where(RunRow.passed.is_(verdict == "passed"))
        if case_id:
            query = query.where(RunRow.case_id == case_id)
        if suite_id:
            suite_key = _uuid(suite_id)
            if suite_key is None:  # no suite can have this id
                return [], 0
            query = query.where(RunRow.suite_id == suite_key)
        with self.session() as session:
            rows, total = _page(
                session, query, (RunRow.started_at.desc(), RunRow.id), limit, offset
            )
            return [_to_summary(r) for r in rows], total

    def mark_interrupted(
        self,
        *,
        stale_after: timedelta = STALE_AFTER,
        alive: Callable[[str | None], bool | None] = owner_alive,
    ) -> dict[str, int]:
        """Turn runs and suites whose process is gone into errors, and only those.

        Several processes share one database (a server, CLI evaluations, another host on
        PostgreSQL), so a `running` row is marked only when its owner is a process on this
        host that no longer exists, or when its owner cannot be checked from here and its
        heartbeat (its start, for rows written before owners were recorded) is older than
        `stale_after`. A marked row gets a finish time, so a late writer is refused instead
        of silently reviving it. Returns rows changed per table.
        """
        now = datetime.now(UTC)
        cutoff = now - stale_after
        runs = _interrupt(
            self.engine,
            RunRow,
            alive,
            cutoff,
            status=RunStatus.ERROR.value,
            error_code="interrupted",
            error=INTERRUPTED,
            ended_at=now,
        )
        suites = _interrupt(
            self.engine,
            SuiteRunRow,
            alive,
            cutoff,
            status="error",
            error=INTERRUPTED,
            finished_at=now,
        )
        return {"runs": runs, "suite_runs": suites}

    # --- datasets ---------------------------------------------------------------------

    def save_dataset(self, dataset: Dataset) -> DatasetSummary:
        """Store a dataset version; saving identical content again is a no-op.

        The content is redacted before it is hashed, so the stored hash always matches the
        stored cases. Raises DatasetConflict when name@version exists with another hash.
        """
        clean = self._scrub(dataset)
        digest = clean.content_hash()
        try:
            return self._insert_dataset(clean, digest)
        except IntegrityError:
            # Another writer stored this name@version between the lookup and the insert.
            return self._insert_dataset(clean, digest)

    def _insert_dataset(self, clean: Dataset, digest: str) -> DatasetSummary:
        with self.session() as session, session.begin():
            row = session.scalar(
                select(DatasetRow).where(
                    DatasetRow.name == clean.name, DatasetRow.version == clean.version
                )
            )
            if row is not None:
                if row.content_hash != digest:
                    raise DatasetConflict(
                        f"dataset {clean.name}@{clean.version} already exists with different "
                        f"content ({row.content_hash}); import the new content as a new version"
                    )
                return _dataset_summary(row)
            row = DatasetRow(
                name=clean.name,
                version=clean.version,
                content_hash=digest,
                description=clean.description,
                agent=clean.agent,
                case_count=len(clean.cases),
                tags=sorted({tag for case in clean.cases for tag in case.tags}),
                # Set fields only (see save_trace); validating it gives back `digest`.
                data=clean.model_dump(mode="json", exclude_unset=True),
                created_at=datetime.now(UTC),
            )
            session.add(row)
        return _dataset_summary(row)

    def get_dataset(self, name: str, version: str) -> DatasetDetail | None:
        return self._dataset_detail(
            select(DatasetRow).where(DatasetRow.name == name, DatasetRow.version == version)
        )

    def latest_dataset(self, name: str) -> DatasetDetail | None:
        """The most recently imported version of a dataset."""
        return self._dataset_detail(
            select(DatasetRow)
            .where(DatasetRow.name == name)
            .order_by(DatasetRow.created_at.desc(), DatasetRow.id.desc())
            .limit(1)
        )

    def list_datasets(self) -> list[DatasetSummary]:
        query = (
            select(DatasetRow)
            .options(defer(DatasetRow.data))
            .order_by(DatasetRow.name, DatasetRow.created_at.desc(), DatasetRow.id.desc())
        )
        with self.session() as session:
            return [_dataset_summary(row) for row in session.scalars(query)]

    def _dataset_detail(self, query: Select[DatasetRow]) -> DatasetDetail | None:
        with self.session() as session:
            row = session.scalar(query)
            if row is None:
                return None
            return DatasetDetail(
                summary=_dataset_summary(row), dataset=Dataset.model_validate(row.data)
            )

    # --- evaluation suites ------------------------------------------------------------

    def create_suite(self, header: SuiteHeader, *, ablation_id: str | None = None) -> None:
        """Insert a `running` suite before its first case runs. `ablation_id` makes it a
        variant suite of that ablation, which lists leave out by default."""
        clean = self._scrub(header)
        row = SuiteRunRow(
            id=uuid.UUID(clean.suite_id),
            label=clean.label,
            status="running",
            dataset_name=clean.dataset_name,
            dataset_version=clean.dataset_version,
            dataset_hash=clean.dataset_hash,
            agent_ref=clean.agent_config.ref,
            config_hash=clean.config_hash,
            effective_config_hash=clean.effective_config_hash,
            provider=clean.environment.provider,
            model=clean.environment.model,
            deterministic=clean.environment.deterministic,
            agent_config=clean.agent_config.model_dump(mode="json"),
            checks_enabled=[name.value for name in clean.checks_enabled],
            environment=clean.environment.model_dump(mode="json"),
            total=0,
            passed=0,
            pass_rate=0.0,
            started_at=clean.started_at,
            owner=process_owner(),
            heartbeat_at=datetime.now(UTC),
            ablation_id=uuid.UUID(ablation_id) if ablation_id else None,
        )
        with self.session() as session, session.begin():
            session.add(row)

    def heartbeat(self, suite_id: str) -> None:
        """Record that this process is still working on a running suite (see liveness)."""
        with self.session() as session, session.begin():
            session.execute(
                update(SuiteRunRow)
                .where(SuiteRunRow.id == uuid.UUID(suite_id), SuiteRunRow.finished_at.is_(None))
                .values(heartbeat_at=datetime.now(UTC))
            )

    def record_case(self, suite_id: str, result: CaseResult) -> None:
        """Persist one verdict as soon as it is known, so a crash keeps finished cases.

        The case's run (if any) must already be saved, and the suite must not be finished.
        """
        clean = self._scrub(result)
        key = uuid.UUID(suite_id)
        with self.session() as session, session.begin():
            suite = _locked_suite(session, key)
            if suite.finished_at is not None:
                raise ValueError(f"suite {key} is already finished; cannot record more cases")
            session.add(_case_row(key, clean))
            session.flush()
            # Counted from the stored rows inside the write transaction rather than
            # incremented in Python: SQLite ignores FOR UPDATE, so a read-modify-write
            # could lose a concurrent update.
            total, passed = session.execute(
                select(
                    func.count(),
                    func.coalesce(func.sum(cast(CaseResultRow.passed, Integer)), 0),
                ).where(CaseResultRow.suite_id == key)
            ).one()
            suite.total = total
            suite.passed = passed
            suite.pass_rate = passed / total
            suite.heartbeat_at = datetime.now(UTC)

    def finish_suite(self, result: SuiteResult) -> None:
        """Store the final status, metrics and gate of a suite created with create_suite.

        Cases not recorded yet are recorded now, so the stored suite always equals `result`.
        A suite that is already finished (e.g. marked interrupted) is refused: its stored
        outcome is never overwritten by a late writer.
        """
        if result.status == "running":
            raise ValueError("finish_suite needs a completed or errored suite")
        clean = self._scrub(result)
        key = uuid.UUID(clean.suite_id)
        with self.session() as session, session.begin():
            suite = _locked_suite(session, key)
            if suite.finished_at is not None:
                raise ValueError(
                    f"suite {key} is already finished ({suite.status}: {suite.error or 'no error'})"
                    "; cannot finish it again"
                )
            recorded = set(
                session.scalars(select(CaseResultRow.case_id).where(CaseResultRow.suite_id == key))
            )
            session.add_all(_case_row(key, c) for c in clean.cases if c.case_id not in recorded)
            suite.status = clean.status
            suite.metrics = clean.metrics.model_dump(mode="json")
            suite.gate = clean.gate.model_dump(mode="json") if clean.gate else None
            suite.gate_passed = clean.gate.passed if clean.gate else None
            suite.total = clean.metrics.total
            suite.passed = clean.metrics.passed
            suite.pass_rate = clean.metrics.pass_rate
            suite.finished_at = clean.finished_at or datetime.now(UTC)
            suite.error = clean.error

    def fail_suite(self, suite_id: str, error: str) -> None:
        """Mark a suite as errored: the harness failed, not the agent."""
        with self.session() as session, session.begin():
            suite = _locked_suite(session, uuid.UUID(suite_id))
            suite.status = "error"
            suite.error = self._redactor.redact_text(error)
            suite.finished_at = datetime.now(UTC)

    def get_suite(self, suite_id: str) -> SuiteResult | None:
        key = _uuid(suite_id)
        if key is None:
            return None
        with self.session() as session:
            row = session.scalar(
                select(SuiteRunRow)
                .where(SuiteRunRow.id == key)
                .options(selectinload(SuiteRunRow.cases))
            )
            if row is None:
                return None
            cases = [_case_result(c) for c in row.cases]
            metrics = (
                SuiteMetrics.model_validate(row.metrics)
                if row.metrics is not None
                else _progress_metrics(cases)
            )
            return SuiteResult.model_validate(
                {
                    "suite_id": str(row.id),
                    "label": row.label,
                    "status": row.status,
                    "dataset_name": row.dataset_name,
                    "dataset_version": row.dataset_version,
                    "dataset_hash": row.dataset_hash,
                    "agent_config": row.agent_config,
                    "config_hash": row.config_hash,
                    "effective_config_hash": row.effective_config_hash,
                    "checks_enabled": row.checks_enabled,
                    "environment": row.environment,
                    "started_at": row.started_at,
                    "finished_at": row.finished_at,
                    "cases": cases,
                    "metrics": metrics,
                    "gate": row.gate,
                    "error": row.error,
                }
            )

    def list_suites(
        self,
        *,
        agent_ref: str | None = None,
        dataset_name: str | None = None,
        status: str | None = None,
        label: str | None = None,
        include_ablations: bool = False,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[SuiteListItem], int]:
        """Newest first. An ablation's variant suites are left out unless `include_ablations`:
        they are reached through their ablation."""
        query = select(SuiteRunRow)
        if not include_ablations:
            query = query.where(SuiteRunRow.ablation_id.is_(None))
        if agent_ref:
            query = query.where(SuiteRunRow.agent_ref == agent_ref)
        if dataset_name:
            query = query.where(SuiteRunRow.dataset_name == dataset_name)
        if status:
            query = query.where(SuiteRunRow.status == status)
        if label is not None:
            query = query.where(SuiteRunRow.label == label)
        with self.session() as session:
            rows, total = _page(
                session,
                query.options(*(defer(column) for column in _SUITE_DOCUMENTS)),
                (SuiteRunRow.started_at.desc(), SuiteRunRow.id),
                limit,
                offset,
            )
            return [_suite_item(r) for r in rows], total

    # --- failure analysis -------------------------------------------------------------

    def latest_suite_ids(self, *, agent_refs: Sequence[str] = ()) -> list[str]:
        """The most recent completed suite of each agent version (optionally only `agent_refs`),
        ordered by agent version. Ablation variants are not versions anyone chose, so they
        never count."""
        query = select(SuiteRunRow.agent_ref, SuiteRunRow.id).where(
            SuiteRunRow.status == "completed", SuiteRunRow.ablation_id.is_(None)
        )
        if agent_refs:
            query = query.where(SuiteRunRow.agent_ref.in_(agent_refs))
        query = query.order_by(SuiteRunRow.agent_ref, SuiteRunRow.started_at.desc(), SuiteRunRow.id)
        latest: dict[str, str] = {}
        with self.session() as session:
            for agent_ref, key in session.execute(query):
                latest.setdefault(agent_ref, str(key))
        return list(latest.values())

    def evaluated_cases(self, suite_ids: Sequence[str]) -> list[EvaluatedCase]:
        """Every case verdict of the given suites, with the faults injected into its run."""
        keys = [key for key in map(_uuid, suite_ids) if key is not None]
        query = (
            select(
                SuiteRunRow.id,
                SuiteRunRow.agent_ref,
                SuiteRunRow.deterministic,
                CaseResultRow,
                RunRow.faults,
                RunRow.model_faults,
            )
            .join(SuiteRunRow, CaseResultRow.suite_id == SuiteRunRow.id)
            .outerjoin(RunRow, CaseResultRow.run_id == RunRow.id)
            .where(CaseResultRow.suite_id.in_(keys))
            .order_by(SuiteRunRow.started_at, SuiteRunRow.id, CaseResultRow.case_id)
        )
        with self.session() as session:
            return [
                EvaluatedCase(
                    suite_id=str(suite_key),
                    agent_ref=agent_ref,
                    deterministic=deterministic,
                    result=_case_result(case_row),
                    fault_kinds=tuple(
                        sorted({fault["kind"] for fault in [*(faults or []), *(model or [])]})
                    ),
                )
                for suite_key, agent_ref, deterministic, case_row, faults, model in session.execute(
                    query
                )
            ]

    def check_stats(self, suite_ids: Sequence[str]) -> dict[str, dict[CheckName, CheckStats]]:
        """Per-suite, per-check verdict counts, computed with one GROUP BY."""
        keys = [key for key in map(_uuid, suite_ids) if key is not None]
        query = (
            select(
                CaseResultRow.suite_id,
                CheckResultRow.check_name,
                CheckResultRow.status,
                func.count(),
            )
            .join(CaseResultRow, CheckResultRow.case_result_id == CaseResultRow.id)
            .where(CaseResultRow.suite_id.in_(keys))
            .group_by(CaseResultRow.suite_id, CheckResultRow.check_name, CheckResultRow.status)
        )
        counts: dict[tuple[str, str], dict[str, int]] = {}
        with self.session() as session:
            for suite_key, check, status, count in session.execute(query):
                counts.setdefault((str(suite_key), check), {})[status] = count
        stats: dict[str, dict[CheckName, CheckStats]] = {}
        for (suite_id, check), by_status in sorted(counts.items()):
            stats.setdefault(suite_id, {})[CheckName(check)] = CheckStats(
                passed=by_status.get(CheckStatus.PASSED, 0),
                failed=by_status.get(CheckStatus.FAILED, 0),
                not_applicable=by_status.get(CheckStatus.NOT_APPLICABLE, 0),
            )
        return stats

    # --- experiments ------------------------------------------------------------------

    def save_experiment(self, comparison: Comparison) -> None:
        clean = self._scrub(comparison)
        changes = Counter(case.change for case in clean.cases)
        row = ExperimentRow(
            id=uuid.UUID(clean.experiment_id),
            name=clean.name,
            created_at=clean.created_at,
            dataset_name=clean.dataset_name,
            dataset_version=clean.dataset_version,
            dataset_hash=clean.dataset_hash,
            suite_a_id=uuid.UUID(clean.a.suite_id),
            suite_b_id=uuid.UUID(clean.b.suite_id),
            comparison=clean.model_dump(mode="json"),
            a_ref=clean.a.agent_ref,
            b_ref=clean.b.agent_ref,
            a_pass_rate=clean.a.metrics.pass_rate,
            b_pass_rate=clean.b.metrics.pass_rate,
            regressions=changes[CaseChange.REGRESSION],
            fixes=changes[CaseChange.FIX],
            confounded=clean.confounded,
            deterministic=clean.deterministic,
        )
        with self.session() as session, session.begin():
            session.add(row)

    def get_experiment(self, experiment_id: str) -> Comparison | None:
        key = _uuid(experiment_id)
        if key is None:
            return None
        with self.session() as session:
            document = session.scalar(
                select(ExperimentRow.comparison).where(ExperimentRow.id == key)
            )
        return Comparison.model_validate(document) if document is not None else None

    def find_experiment(self, suite_a: str, suite_b: str) -> Comparison | None:
        """The most recent stored comparison of exactly these suites (A is the reference)."""
        key_a, key_b = _uuid(suite_a), _uuid(suite_b)
        if key_a is None or key_b is None:
            return None
        with self.session() as session:
            document = session.scalar(
                select(ExperimentRow.comparison)
                .where(ExperimentRow.suite_a_id == key_a, ExperimentRow.suite_b_id == key_b)
                .order_by(ExperimentRow.created_at.desc(), ExperimentRow.id)
                .limit(1)
            )
        return Comparison.model_validate(document) if document is not None else None

    def list_experiments(
        self, *, name: str | None = None, limit: int = 50, offset: int = 0
    ) -> tuple[list[ExperimentListItem], int]:
        query = select(ExperimentRow)
        if name is not None:
            query = query.where(ExperimentRow.name == name)
        with self.session() as session:
            rows, total = _page(
                session,
                query.options(defer(ExperimentRow.comparison)),
                (ExperimentRow.created_at.desc(), ExperimentRow.id),
                limit,
                offset,
            )
            return [_experiment_item(r) for r in rows], total

    # --- ablations --------------------------------------------------------------------

    def save_ablation(self, ablation: Ablation) -> None:
        clean = self._scrub(ablation)
        row = AblationRow(
            id=uuid.UUID(clean.ablation_id),
            name=clean.name,
            created_at=clean.created_at,
            experiment_id=uuid.UUID(clean.experiment_id) if clean.experiment_id else None,
            dataset_name=clean.dataset_name,
            dataset_version=clean.dataset_version,
            dataset_hash=clean.dataset_hash,
            a_suite_id=uuid.UUID(clean.a.suite_id),
            b_suite_id=uuid.UUID(clean.b.suite_id),
            a_ref=clean.a.agent_ref,
            b_ref=clean.b.agent_ref,
            deterministic=clean.deterministic,
            total_effect_pp=clean.total_effect_pp,
            interaction_pp=clean.interaction_pp,
            dimensions=list(clean.dimensions),
            result=clean.model_dump(mode="json"),
        )
        with self.session() as session, session.begin():
            session.add(row)

    def get_ablation(self, ablation_id: str) -> Ablation | None:
        key = _uuid(ablation_id)
        if key is None:
            return None
        with self.session() as session:
            found = session.execute(
                select(AblationRow.result, AblationRow.experiment_id).where(AblationRow.id == key)
            ).one_or_none()
        if found is None:
            return None
        document, experiment = found
        ablation = Ablation.model_validate(document)
        if experiment is None and ablation.experiment_id is not None:
            # The experiment was deleted on its own: the link to it would dangle.
            return ablation.model_copy(update={"experiment_id": None})
        return ablation

    def list_ablations(
        self, *, experiment_id: str | None = None, limit: int = 50, offset: int = 0
    ) -> tuple[list[AblationListItem], int]:
        """Newest first; with `experiment_id`, only those explaining that comparison."""
        query = select(AblationRow)
        if experiment_id is not None:
            key = _uuid(experiment_id)
            if key is None:  # no experiment can have this id
                return [], 0
            query = query.where(AblationRow.experiment_id == key)
        with self.session() as session:
            rows, total = _page(
                session,
                query.options(defer(AblationRow.result)),
                (AblationRow.created_at.desc(), AblationRow.id),
                limit,
                offset,
            )
            return [_ablation_item(r) for r in rows], total

    def latest_ablation_for_experiment(self, experiment_id: str) -> Ablation | None:
        key = _uuid(experiment_id)
        if key is None:
            return None
        with self.session() as session:
            document = session.scalar(
                select(AblationRow.result)
                .where(AblationRow.experiment_id == key)
                .order_by(AblationRow.created_at.desc(), AblationRow.id)
                .limit(1)
            )
        return Ablation.model_validate(document) if document is not None else None

    # --- inventory and purge ----------------------------------------------------------

    def inventory(self) -> StorageInventory:
        """What is stored, where, and how much space it takes."""
        with self.engine.connect() as connection:
            counts = _row_counts(connection)
        url = self.engine.url
        sqlite = url.get_backend_name() == "sqlite"
        return StorageInventory(
            backend="sqlite" if sqlite else "postgresql",
            # Where the data lives, never the user or password.
            location=(
                (url.database or ":memory:")
                if sqlite
                else f"{url.host or 'localhost'}:{url.port or 5432}/{url.database or ''}"
            ),
            size_bytes=self._size_bytes(),
            tables=[
                TableInventory(
                    name=model.__tablename__, rows=counts[model.__tablename__], description=d
                )
                for model, d in _INVENTORY
            ],
        )

    def purge(
        self,
        scope: PurgeScope = "all",
        before: datetime | None = None,
        *,
        keep_datasets: Collection[str] = (),
    ) -> PurgeResult:
        """Delete stored data, then compact so the content is gone from disk, not just unlinked.

        `before` limits deletion to rows created (runs and suites: started) before it. A suite
        takes its case results, runs, experiments and ablations with it; purging experiments
        purges ablations too; a deleted ablation takes its variant suites; purging runs keeps
        verdicts but unlinks them. Datasets whose content hash is in `keep_datasets` (those
        packaged with AgentScope) are never deleted. SQLite overwrites deleted cells
        (secure_delete), VACUUM rebuilds the file and the WAL is truncated; PostgreSQL
        rewrites the affected tables (VACUUM FULL).

        Raises PurgeIncomplete (carrying the result) when SQLite could not truncate the WAL
        because another connection was reading: the rows are gone, but old copies of their
        pages are still on disk until a later purge succeeds.
        """
        if before is not None and before.tzinfo is None:
            raise ValueError("before must be timezone-aware")
        scopes = list(_PURGE_TARGETS) if scope == "all" else [scope]
        targets = [target for name in scopes for target in _PURGE_TARGETS[name]]
        size_before = self._size_bytes()
        with self.engine.begin() as connection:
            if connection.dialect.name == "sqlite":
                # pysqlite would run the first counts outside the transaction; take the write
                # lock first so the counts and the deletes see the same rows.
                connection.exec_driver_sql("BEGIN IMMEDIATE")
            counts_before = _row_counts(connection)
            ablations = set(connection.scalars(select(AblationRow.id)))
            for model, moment in targets:
                statement = delete(model)
                if before is not None:
                    statement = statement.where(moment < before)
                if model is DatasetRow and keep_datasets:
                    statement = statement.where(DatasetRow.content_hash.not_in(keep_datasets))
                connection.execute(statement)
            _delete_orphaned_variants(connection, ablations)
            counts_after = _row_counts(connection)
        deleted = {name: max(0, counts_before[name] - counts_after[name]) for name in counts_before}
        erased = self._compact([name for name, count in deleted.items() if count])
        result = PurgeResult(
            deleted=deleted, size_bytes_before=size_before, size_bytes_after=self._size_bytes()
        )
        if not erased:
            raise PurgeIncomplete(
                "rows were deleted, but another connection is reading the database, so the "
                "write-ahead log still holds old copies of them; close other AgentScope "
                "processes and purge again",
                result,
            )
        return result

    def _compact(self, tables: list[str]) -> bool:
        """Rewrite storage so deleted content is gone from disk. False if SQLite could not."""
        # Both statements refuse to run inside a transaction.
        with self.engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
            if connection.dialect.name == "sqlite":
                connection.exec_driver_sql("VACUUM")
                # Returns (busy, wal frames, checkpointed frames). busy=1 means a reader kept
                # the WAL from being folded back and truncated; SQLite reports it, not raises.
                busy = connection.exec_driver_sql("PRAGMA wal_checkpoint(TRUNCATE)").one()[0]
                return not busy
            quote = connection.dialect.identifier_preparer.quote
            for table in tables:
                connection.exec_driver_sql(f"VACUUM FULL {quote(table)}")
            return True

    def _size_bytes(self) -> int | None:
        """The SQLite file plus its WAL, or the PostgreSQL tables with their indexes."""
        url = self.engine.url
        if url.get_backend_name() == "sqlite":
            if not url.database or url.database == ":memory:":
                return None
            path = Path(url.database)
            files = (path, path.with_name(path.name + "-wal"))
            return sum(f.stat().st_size for f in files if f.exists())
        with self.engine.connect() as connection:
            return sum(
                connection.scalar(
                    text("SELECT pg_total_relation_size(to_regclass(:name))"),
                    {"name": model.__tablename__},
                )
                or 0
                for model, _ in _INVENTORY
            )


def _page[R](
    session: Session,
    query: Select[R],
    order: Sequence[Any],
    limit: int,
    offset: int,
) -> tuple[list[R], int]:
    """A page and the total number of matching rows, from one statement.

    A separate COUNT and page query each see their own snapshot (pysqlite starts no
    transaction for reads, PostgreSQL's READ COMMITTED takes one per statement), so a row
    committed in between made a page longer than its total. A window count over the same
    statement cannot disagree with the rows it returns.
    """
    rows = session.execute(
        query.add_columns(func.count().over()).order_by(*order).limit(limit).offset(offset)
    ).all()
    if rows:
        return [row[0] for row in rows], rows[0][1]
    if offset == 0:
        return [], 0
    # Past the end: there is no row to carry the count, so ask for it separately.
    return [], session.scalar(select(func.count()).select_from(query.subquery())) or 0


def _interrupt(
    engine: Engine,
    model: type[RunRow] | type[SuiteRunRow],
    alive: Callable[[str | None], bool | None],
    cutoff: datetime,
    **values: Any,
) -> int:
    """Mark the `running` rows of one table whose owner is dead, or unknown and silent."""
    running = RunStatus.RUNNING.value
    with engine.connect() as connection:
        candidates = connection.execute(
            select(model.id, model.owner).where(model.status == running)
        ).all()
    states = [(key, alive(owner)) for key, owner in candidates]
    dead = [key for key, state in states if state is False]
    unknown = [key for key, state in states if state is None]
    changed = 0
    with engine.begin() as connection:
        if dead:
            changed += connection.execute(
                update(model).where(model.id.in_(dead), model.status == running).values(**values)
            ).rowcount
        if unknown:
            last_seen = func.coalesce(model.heartbeat_at, model.started_at)
            changed += connection.execute(
                update(model)
                .where(model.id.in_(unknown), model.status == running, last_seen < cutoff)
                .values(**values)
            ).rowcount
    return changed


def _cut_overlong_strings(document: Any, exc: ValidationError) -> bool:
    """Cut every string `exc` reports as too long back to its limit, in place, ending it
    with TRUNCATED. False when `exc` reports anything else (a real defect, not redaction)."""
    errors = exc.errors()
    if not errors or any(error["type"] != "string_too_long" for error in errors):
        return False
    for error in errors:
        limit = int((error.get("ctx") or {})["max_length"])
        container, key = _locate(document, error["loc"])
        if container is None or not isinstance(container[key], str):
            return False
        value: str = container[key]
        keep = limit - len(TRUNCATED)
        container[key] = value[:keep] + TRUNCATED if keep > 0 else value[:limit]
    return True


def _locate(document: Any, loc: Sequence[int | str]) -> tuple[Any, Any]:
    """The container and key a validation error location points at. Parts that name no
    key (union members such as `str` in a JsonValue) are skipped."""
    container: Any = None
    key: Any = None
    current = document
    for part in loc:
        in_dict = isinstance(current, dict) and part in current
        in_list = isinstance(current, list) and isinstance(part, int) and 0 <= part < len(current)
        if in_dict or in_list:
            container, key, current = current, part, current[part]
    return container, key


def _delete_orphaned_variants(connection: Connection, ablations: set[uuid.UUID]) -> None:
    """Delete the variant suites of every ablation in `ablations` that is gone. Repeated
    until none is: a variant suite can be the A or B of another ablation, and deleting it
    cascades to that one."""
    while True:
        remaining = set(connection.scalars(select(AblationRow.id)))
        gone = ablations - remaining
        if not gone:
            return
        connection.execute(delete(SuiteRunRow).where(SuiteRunRow.ablation_id.in_(gone)))
        ablations = remaining


def _locked_suite(session: Session, key: uuid.UUID) -> SuiteRunRow:
    suite = session.get(SuiteRunRow, key, with_for_update=True)
    if suite is None:
        raise LookupError(f"suite {key} not found")
    return suite


def _row_counts(connection: Connection) -> dict[str, int]:
    return {
        model.__tablename__: connection.scalar(select(func.count()).select_from(model)) or 0
        for model, _ in _INVENTORY
    }


def _case_row(suite_id: uuid.UUID, result: CaseResult) -> CaseResultRow:
    return CaseResultRow(
        suite_id=suite_id,
        case_id=result.case_id,
        case_hash=result.case_hash,
        run_id=uuid.UUID(result.run_id) if result.run_id else None,
        passed=result.passed,
        status=result.status.value if result.status else None,
        harness_error=result.harness_error,
        result=result.model_dump(mode="json"),
        checks=[
            CheckResultRow(check_name=c.name.value, status=c.status.value, reason=c.reason.value)
            for c in result.checks
        ],
    )


def _case_result(row: CaseResultRow) -> CaseResult:
    result = CaseResult.model_validate(row.result)
    if row.run_id is None and result.run_id is not None:
        # The run was purged: the verdict stays, the link to it would dangle.
        return result.model_copy(update={"run_id": None, "trace_id": None})
    return result


def _progress_metrics(cases: list[CaseResult]) -> SuiteMetrics:
    """Counts over the cases recorded so far, for a suite that has not finished.

    A finished suite returns the metrics its evaluator stored. Usage and timings stay unset:
    they are not part of a case verdict, and absent is better than invented.
    """
    total = len(cases)
    passed = sum(c.passed for c in cases)
    outcomes = Counter((check.name, check.status) for c in cases for check in c.checks)
    return SuiteMetrics(
        total=total,
        passed=passed,
        failed=sum(not c.passed and c.harness_error is None for c in cases),
        harness_errors=sum(c.harness_error is not None for c in cases),
        pass_rate=passed / total if total else 0.0,
        run_statuses=dict(Counter(c.status.value for c in cases if c.status is not None)),
        checks={
            name: CheckStats(
                passed=outcomes[name, CheckStatus.PASSED],
                failed=outcomes[name, CheckStatus.FAILED],
                not_applicable=outcomes[name, CheckStatus.NOT_APPLICABLE],
            )
            for name in sorted({name for name, _ in outcomes})
        },
        injected_fault_outcomes=sum(c.injected_fault_outcomes for c in cases),
        unexpected_tool_errors=sum(c.unexpected_tool_errors for c in cases),
        invalid_outputs=sum(c.invalid_output for c in cases),
        repaired_outputs=sum(c.repaired_output for c in cases),
        policy_violation_cases=sum(
            any(
                check.name is CheckName.POLICY_COMPLIANCE and check.status is CheckStatus.FAILED
                for check in c.checks
            )
            for c in cases
        ),
        unauthorized_tool_attempts=sum(c.unauthorized_tool_attempts for c in cases),
        unauthorized_tool_executions=sum(c.unauthorized_tool_executions for c in cases),
        avg_steps=sum(c.steps for c in cases) / total if total else 0.0,
    )


def _dataset_summary(row: DatasetRow) -> DatasetSummary:
    return DatasetSummary(
        name=row.name,
        version=row.version,
        content_hash=row.content_hash,
        description=row.description,
        agent=row.agent,
        case_count=row.case_count,
        tags=row.tags,
        created_at=row.created_at,
    )


def _suite_item(row: SuiteRunRow) -> SuiteListItem:
    return SuiteListItem.model_validate(
        {
            "suite_id": str(row.id),
            "label": row.label,
            "status": row.status,
            "dataset_name": row.dataset_name,
            "dataset_version": row.dataset_version,
            "dataset_hash": row.dataset_hash,
            "agent_ref": row.agent_ref,
            "config_hash": row.config_hash,
            "provider": row.provider,
            "model": row.model,
            "deterministic": row.deterministic,
            "started_at": row.started_at,
            "finished_at": row.finished_at,
            "total": row.total,
            "passed": row.passed,
            "pass_rate": row.pass_rate,
            "gate_passed": row.gate_passed,
        }
    )


def _experiment_item(row: ExperimentRow) -> ExperimentListItem:
    return ExperimentListItem(
        experiment_id=str(row.id),
        name=row.name,
        created_at=row.created_at,
        dataset_name=row.dataset_name,
        dataset_version=row.dataset_version,
        a_ref=row.a_ref,
        b_ref=row.b_ref,
        a_pass_rate=row.a_pass_rate,
        b_pass_rate=row.b_pass_rate,
        regressions=row.regressions,
        fixes=row.fixes,
        confounded=row.confounded,
        deterministic=row.deterministic,
    )


def _ablation_item(row: AblationRow) -> AblationListItem:
    return AblationListItem.model_validate(
        {
            "ablation_id": str(row.id),
            "name": row.name,
            "created_at": row.created_at,
            "experiment_id": str(row.experiment_id) if row.experiment_id else None,
            "dataset_name": row.dataset_name,
            "dataset_version": row.dataset_version,
            "a_ref": row.a_ref,
            "b_ref": row.b_ref,
            "dimensions": row.dimensions,
            "total_effect_pp": row.total_effect_pp,
            "interaction_pp": row.interaction_pp,
            "deterministic": row.deterministic,
        }
    )


def _to_summary(row: RunRow) -> RunSummary:
    return RunSummary(
        run_id=str(row.id),
        trace_id=row.trace_id,
        agent=row.agent,
        agent_version=row.agent_version,
        config_hash=row.config_hash,
        provider=row.provider,
        model=row.model,
        status=RunStatus(row.status),
        error_code=row.error_code,
        task=row.task,
        case_id=row.case_id,
        suite_id=str(row.suite_id) if row.suite_id else None,
        started_at=row.started_at,
        duration_ms=row.duration_ms,
        steps=row.steps,
        tool_calls=row.tool_calls,
        blocked_tool_calls=row.blocked_tool_calls,
        guardrail_events=row.guardrail_count,
        faults=row.fault_count,
        passed=row.passed,
    )


def _to_trace(row: RunRow) -> TraceRecord:
    data: dict[str, Any] = {
        "trace_id": row.trace_id,
        "run_id": str(row.id),
        "agent": row.agent,
        "agent_version": row.agent_version,
        "config_hash": row.config_hash,
        "effective_config_hash": row.effective_config_hash,
        "config": row.config,
        "limits": row.limits,
        "permitted_tools": row.permitted_tools,
        "task": row.task,
        "status": row.status,
        "error_code": row.error_code,
        "error": row.error,
        "started_at": row.started_at,
        "ended_at": row.ended_at or row.started_at,
        "duration_ms": row.duration_ms,
        "steps": row.steps,
        "provider": row.provider,
        "model": row.model,
        "final_output": row.final_output,
        "usage": row.usage,
        "cost": row.cost,
        "faults": row.faults,
        "model_faults": row.model_faults,
        "case_id": row.case_id,
        "transitions": row.transitions,
        "tool_invocations": row.tool_invocations,
        "guardrail_events": row.guardrail_events,
        "spans": [
            SpanRecord(
                trace_id=s.trace_id,
                span_id=s.span_id,
                parent_span_id=s.parent_span_id,
                name=s.name,
                kind=SpanKind(s.kind),
                status=SpanStatus(s.status),
                status_message=s.status_message,
                start_time=_from_ns(s.start_time_unix_nano),
                end_time=_from_ns(s.end_time_unix_nano),
                duration_ms=s.duration_ms,
                attributes=s.attributes,
                input=s.input,
                output=s.output,
                events=[SpanEvent.model_validate(e) for e in s.events],
            )
            for s in row.spans
        ],
    }
    return TraceRecord.model_validate(data)
