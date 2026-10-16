"""Runs and spans.

Revision ID: 0001
Revises:
Create Date: 2026-10-16
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from agentscope.storage.types import JSONType, UTCDateTime

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("trace_id", sa.String(32), nullable=False),
        sa.Column("agent", sa.String(64), nullable=False),
        sa.Column("agent_version", sa.String(64), nullable=False),
        sa.Column("config_hash", sa.String(80), nullable=False),
        sa.Column("effective_config_hash", sa.String(80), nullable=False),
        sa.Column("provider", sa.String(64), nullable=False),
        sa.Column("model", sa.String(128), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("error_code", sa.String(48), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("task", sa.Text(), nullable=False),
        sa.Column("case_id", sa.String(64), nullable=True),
        sa.Column("suite_id", sa.Uuid(), nullable=True),
        sa.Column("started_at", UTCDateTime(), nullable=False),
        sa.Column("ended_at", UTCDateTime(), nullable=True),
        sa.Column("duration_ms", sa.Float(), nullable=False),
        sa.Column("steps", sa.Integer(), nullable=False),
        sa.Column("tool_calls", sa.Integer(), nullable=False),
        sa.Column("blocked_tool_calls", sa.Integer(), nullable=False),
        sa.Column("guardrail_count", sa.Integer(), nullable=False),
        sa.Column("fault_count", sa.Integer(), nullable=False),
        sa.Column("config", JSONType, nullable=False),
        sa.Column("limits", JSONType, nullable=False),
        sa.Column("permitted_tools", JSONType, nullable=False),
        sa.Column("final_output", JSONType, nullable=True),
        sa.Column("usage", JSONType, nullable=True),
        sa.Column("cost", JSONType, nullable=True),
        sa.Column("faults", JSONType, nullable=False),
        sa.Column("model_faults", JSONType, nullable=False),
        sa.Column("transitions", JSONType, nullable=False),
        sa.Column("tool_invocations", JSONType, nullable=False),
        sa.Column("guardrail_events", JSONType, nullable=False),
        sa.Column("eval_case", JSONType, nullable=True),
        sa.Column("evaluation", JSONType, nullable=True),
        sa.Column("passed", sa.Boolean(), nullable=True),
        sa.Column("created_at", UTCDateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_runs")),
        sa.UniqueConstraint("trace_id", name=op.f("uq_runs_trace_id")),
    )
    for column in ("agent", "config_hash", "status", "case_id", "suite_id", "started_at"):
        op.create_index(op.f(f"ix_runs_{column}"), "runs", [column])

    op.create_table(
        "spans",
        sa.Column("trace_id", sa.String(32), nullable=False),
        sa.Column("span_id", sa.String(16), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("parent_span_id", sa.String(16), nullable=True),
        sa.Column("name", sa.String(256), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("status", sa.String(8), nullable=False),
        sa.Column("status_message", sa.Text(), nullable=True),
        sa.Column("start_time_unix_nano", sa.BigInteger(), nullable=False),
        sa.Column("end_time_unix_nano", sa.BigInteger(), nullable=False),
        sa.Column("duration_ms", sa.Float(), nullable=False),
        sa.Column("attributes", JSONType, nullable=False),
        sa.Column("input", JSONType, nullable=True),
        sa.Column("output", JSONType, nullable=True),
        sa.Column("events", JSONType, nullable=False),
        sa.ForeignKeyConstraint(
            ["run_id"], ["runs.id"], name=op.f("fk_spans_run_id_runs"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("trace_id", "span_id", name=op.f("pk_spans")),
    )
    op.create_index("ix_spans_run_seq", "spans", ["run_id", "seq"])


def downgrade() -> None:
    op.drop_index("ix_spans_run_seq", table_name="spans")
    op.drop_table("spans")
    for column in ("agent", "config_hash", "status", "case_id", "suite_id", "started_at"):
        op.drop_index(op.f(f"ix_runs_{column}"), table_name="runs")
    op.drop_table("runs")
