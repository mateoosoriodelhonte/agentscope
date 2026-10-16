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
    Uuid,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from agentscope.storage.types import JSONType, UTCDateTime

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
    suite_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, index=True)
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
