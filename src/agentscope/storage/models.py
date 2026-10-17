"""ORM models. Migrations never import this module; they use storage.types directly."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    Float,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from agentscope.storage.types import BigIntPK, JSONType, UTCDateTime

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class RunRow(Base):
    __tablename__ = "runs"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    trace_id: Mapped[str] = mapped_column(String(32), unique=True)
    agent: Mapped[str] = mapped_column(String(64), index=True)
    agent_version: Mapped[str] = mapped_column(String(64))
    config_hash: Mapped[str] = mapped_column(String(80), index=True)
    effective_config_hash: Mapped[str] = mapped_column(String(80))
    provider: Mapped[str] = mapped_column(String(64))
    model: Mapped[str] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(24), index=True)
    error_code: Mapped[str | None] = mapped_column(String(48))
    error: Mapped[str | None] = mapped_column(Text)
    task: Mapped[str] = mapped_column(Text)
    case_id: Mapped[str | None] = mapped_column(String(64), index=True)
    # A suite's traces go with it when the suite is deleted.
    suite_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("suite_runs.id", ondelete="CASCADE"), index=True
    )
    started_at: Mapped[datetime] = mapped_column(UTCDateTime, index=True)
    ended_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    duration_ms: Mapped[float] = mapped_column(Float)
    steps: Mapped[int] = mapped_column(Integer)
    tool_calls: Mapped[int] = mapped_column(Integer)
    blocked_tool_calls: Mapped[int] = mapped_column(Integer)
    guardrail_count: Mapped[int] = mapped_column(Integer)
    fault_count: Mapped[int] = mapped_column(Integer)
    config: Mapped[dict[str, Any]] = mapped_column(JSONType)
    limits: Mapped[dict[str, Any]] = mapped_column(JSONType)
    permitted_tools: Mapped[list[str]] = mapped_column(JSONType)
    final_output: Mapped[Any | None] = mapped_column(JSONType)
    usage: Mapped[dict[str, Any] | None] = mapped_column(JSONType)
    cost: Mapped[dict[str, Any] | None] = mapped_column(JSONType)
    faults: Mapped[list[Any]] = mapped_column(JSONType)
    model_faults: Mapped[list[Any]] = mapped_column(JSONType)
    transitions: Mapped[list[Any]] = mapped_column(JSONType)
    tool_invocations: Mapped[list[Any]] = mapped_column(JSONType)
    guardrail_events: Mapped[list[Any]] = mapped_column(JSONType)
    # The test case this run was evaluated against (ad-hoc or from a dataset) and the
    # resulting CaseResult, so a trace can always be shown with its verdict.
    eval_case: Mapped[dict[str, Any] | None] = mapped_column(JSONType)
    evaluation: Mapped[dict[str, Any] | None] = mapped_column(JSONType)
    passed: Mapped[bool | None] = mapped_column(Boolean)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime)
    # Liveness of a row stored `running` (see storage/liveness.py): the process writing it
    # ("host:pid:incarnation") and when it last reported. NULL for runs stored finished.
    owner: Mapped[str | None] = mapped_column(String(300))
    heartbeat_at: Mapped[datetime | None] = mapped_column(UTCDateTime)

    spans: Mapped[list[SpanRow]] = relationship(
        back_populates="run",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="SpanRow.seq",
    )


class SpanRow(Base):
    __tablename__ = "spans"
    __table_args__ = (Index("ix_spans_run_seq", "run_id", "seq"),)

    trace_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    span_id: Mapped[str] = mapped_column(String(16), primary_key=True)
    run_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("runs.id", ondelete="CASCADE"))
    seq: Mapped[int] = mapped_column(Integer)
    parent_span_id: Mapped[str | None] = mapped_column(String(16))
    name: Mapped[str] = mapped_column(String(256))
    kind: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(8))
    status_message: Mapped[str | None] = mapped_column(Text)
    start_time_unix_nano: Mapped[int] = mapped_column(BigInteger)
    end_time_unix_nano: Mapped[int] = mapped_column(BigInteger)
    duration_ms: Mapped[float] = mapped_column(Float)
    attributes: Mapped[dict[str, Any]] = mapped_column(JSONType)
    input: Mapped[Any | None] = mapped_column(JSONType)
    output: Mapped[Any | None] = mapped_column(JSONType)
    events: Mapped[list[Any]] = mapped_column(JSONType)

    run: Mapped[RunRow] = relationship(back_populates="spans")


class DatasetRow(Base):
    __tablename__ = "datasets"
    # A name@version is immutable: re-importing different content under it is a conflict.
    __table_args__ = (UniqueConstraint("name", "version", name="uq_datasets_name_version"),)

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True)
    name: Mapped[str] = mapped_column(String(64))
    version: Mapped[str] = mapped_column(String(64))
    content_hash: Mapped[str] = mapped_column(String(80))
    description: Mapped[str] = mapped_column(Text)
    agent: Mapped[str | None] = mapped_column(String(64))
    case_count: Mapped[int] = mapped_column(Integer)
    tags: Mapped[list[str]] = mapped_column(JSONType)
    data: Mapped[dict[str, Any]] = mapped_column(JSONType)  # the full dataset, as hashed
    created_at: Mapped[datetime] = mapped_column(UTCDateTime)


class SuiteRunRow(Base):
    __tablename__ = "suite_runs"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    label: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(24))
    dataset_name: Mapped[str] = mapped_column(String(64), index=True)
    dataset_version: Mapped[str] = mapped_column(String(64))
    dataset_hash: Mapped[str] = mapped_column(String(80))
    # "<agent>@<version>": two 64-character identifiers and the separator.
    agent_ref: Mapped[str] = mapped_column(String(130), index=True)
    config_hash: Mapped[str] = mapped_column(String(80))
    effective_config_hash: Mapped[str] = mapped_column(String(80))
    provider: Mapped[str] = mapped_column(String(64))
    model: Mapped[str] = mapped_column(String(128))
    deterministic: Mapped[bool] = mapped_column(Boolean)
    agent_config: Mapped[dict[str, Any]] = mapped_column(JSONType)
    checks_enabled: Mapped[list[str]] = mapped_column(JSONType)
    environment: Mapped[dict[str, Any]] = mapped_column(JSONType)
    # Written by finish_suite; NULL while running and for suites that never finished.
    metrics: Mapped[dict[str, Any] | None] = mapped_column(JSONType)
    gate: Mapped[dict[str, Any] | None] = mapped_column(JSONType)
    gate_passed: Mapped[bool | None] = mapped_column(Boolean)
    total: Mapped[int] = mapped_column(Integer)
    passed: Mapped[int] = mapped_column(Integer)
    pass_rate: Mapped[float] = mapped_column(Float)
    started_at: Mapped[datetime] = mapped_column(UTCDateTime, index=True)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    error: Mapped[str | None] = mapped_column(Text)
    # The process running the suite ("host:pid:incarnation") and its last heartbeat, so only
    # suites whose process is gone or silent are marked interrupted (see storage/liveness.py).
    owner: Mapped[str | None] = mapped_column(String(300))
    heartbeat_at: Mapped[datetime | None] = mapped_column(UTCDateTime)

    cases: Mapped[list[CaseResultRow]] = relationship(
        back_populates="suite",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="CaseResultRow.case_id",
    )


class CaseResultRow(Base):
    __tablename__ = "case_results"
    __table_args__ = (
        UniqueConstraint("suite_id", "case_id", name="uq_case_results_suite_id_case_id"),
    )

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True)
    suite_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("suite_runs.id", ondelete="CASCADE")
    )
    case_id: Mapped[str] = mapped_column(String(64))
    case_hash: Mapped[str] = mapped_column(String(80))
    # Purging traces keeps the verdict but drops the link to the deleted run.
    run_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("runs.id", ondelete="SET NULL"), index=True
    )
    passed: Mapped[bool] = mapped_column(Boolean)
    status: Mapped[str | None] = mapped_column(String(24))
    harness_error: Mapped[str | None] = mapped_column(Text)
    result: Mapped[dict[str, Any]] = mapped_column(JSONType)  # the full CaseResult

    suite: Mapped[SuiteRunRow] = relationship(back_populates="cases")
    checks: Mapped[list[CheckResultRow]] = relationship(
        cascade="all, delete-orphan", passive_deletes=True
    )


class CheckResultRow(Base):
    """One row per (case, check), so per-check rates are a portable GROUP BY."""

    __tablename__ = "check_results"

    case_result_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("case_results.id", ondelete="CASCADE"), primary_key=True
    )
    check_name: Mapped[str] = mapped_column(String(32), primary_key=True)
    status: Mapped[str] = mapped_column(String(16))
    reason: Mapped[str] = mapped_column(String(48))


class ExperimentRow(Base):
    __tablename__ = "experiments"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    name: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime)
    dataset_name: Mapped[str] = mapped_column(String(64))
    dataset_version: Mapped[str] = mapped_column(String(64))
    dataset_hash: Mapped[str] = mapped_column(String(80))
    suite_a_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("suite_runs.id", ondelete="CASCADE")
    )
    suite_b_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("suite_runs.id", ondelete="CASCADE")
    )
    comparison: Mapped[dict[str, Any]] = mapped_column(JSONType)  # the full Comparison
    # Denormalized for the list view, so it never loads the comparison documents.
    a_ref: Mapped[str] = mapped_column(String(130))
    b_ref: Mapped[str] = mapped_column(String(130))
    a_pass_rate: Mapped[float] = mapped_column(Float)
    b_pass_rate: Mapped[float] = mapped_column(Float)
    regressions: Mapped[int] = mapped_column(Integer)
    fixes: Mapped[int] = mapped_column(Integer)
    confounded: Mapped[bool] = mapped_column(Boolean)
    deterministic: Mapped[bool] = mapped_column(Boolean)
