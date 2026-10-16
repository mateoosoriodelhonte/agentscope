"""The single persistence choke point. Every write is scrubbed by the redactor first."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session, selectinload, sessionmaker

from agentscope.domain.datasets import EvalCase
from agentscope.domain.evaluation import CaseResult
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
from agentscope.storage.models import RunRow, SpanRow
from agentscope.tracing.redaction import Redactor


def _ns(value: datetime) -> int:
    return int(value.timestamp() * 1_000_000) * 1000


def _from_ns(value: int) -> datetime:
    return datetime.fromtimestamp(value / 1e9, tz=UTC)


class Repository:
    def __init__(self, engine: Engine, redactor: Redactor | None = None) -> None:
        self.engine = engine
        self._sessions: sessionmaker[Session] = session_factory(engine)
        self._redactor = redactor or Redactor()

    def session(self) -> Session:
        return self._sessions()

    # --- runs -------------------------------------------------------------------------

    def save_trace(
        self,
        trace: TraceRecord,
        *,
        suite_id: str | None = None,
        case: EvalCase | None = None,
        evaluation: CaseResult | None = None,
    ) -> None:
        clean = TraceRecord.model_validate(self._redactor.redact(trace.model_dump(mode="json")))
        clean_eval = (
            CaseResult.model_validate(self._redactor.redact(evaluation.model_dump(mode="json")))
            if evaluation
            else None
        )
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
            eval_case=case.model_dump(mode="json") if case else None,
            evaluation=clean_eval.model_dump(mode="json") if clean_eval else None,
            passed=clean_eval.passed if clean_eval else None,
            created_at=datetime.now(UTC),
        )
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
        try:
            key = uuid.UUID(run_id)
        except ValueError:
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
        status: str | None = None,
        case_id: str | None = None,
        suite_id: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[RunSummary], int]:
        query = select(RunRow)
        if agent:
            query = query.where(RunRow.agent == agent)
        if status:
            query = query.where(RunRow.status == status)
        if case_id:
            query = query.where(RunRow.case_id == case_id)
        if suite_id:
            query = query.where(RunRow.suite_id == uuid.UUID(suite_id))
        with self.session() as session:
            total = session.scalar(select(func.count()).select_from(query.subquery())) or 0
            rows = session.scalars(
                query.order_by(RunRow.started_at.desc(), RunRow.id).limit(limit).offset(offset)
            ).all()
            return [_to_summary(r) for r in rows], total


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
