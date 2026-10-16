"""Datasets, evaluation suites, per-case and per-check results, and experiments.

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-16
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from agentscope.storage.types import BigIntPK, JSONType, UTCDateTime

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SUITE_INDEXES = ("started_at", "agent_ref", "dataset_name")


def upgrade() -> None:
    op.create_table(
        "datasets",
        sa.Column("id", BigIntPK, nullable=False),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("version", sa.String(64), nullable=False),
        sa.Column("content_hash", sa.String(80), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("agent", sa.String(64), nullable=True),
        sa.Column("case_count", sa.Integer(), nullable=False),
        sa.Column("tags", JSONType, nullable=False),
        sa.Column("data", JSONType, nullable=False),
        sa.Column("created_at", UTCDateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_datasets")),
        sa.UniqueConstraint("name", "version", name="uq_datasets_name_version"),
    )

    op.create_table(
        "suite_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("label", sa.Text(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("dataset_name", sa.String(64), nullable=False),
        sa.Column("dataset_version", sa.String(64), nullable=False),
        sa.Column("dataset_hash", sa.String(80), nullable=False),
        sa.Column("agent_ref", sa.String(130), nullable=False),
        sa.Column("config_hash", sa.String(80), nullable=False),
        sa.Column("effective_config_hash", sa.String(80), nullable=False),
        sa.Column("provider", sa.String(64), nullable=False),
        sa.Column("model", sa.String(128), nullable=False),
        sa.Column("deterministic", sa.Boolean(), nullable=False),
        sa.Column("agent_config", JSONType, nullable=False),
        sa.Column("checks_enabled", JSONType, nullable=False),
        sa.Column("environment", JSONType, nullable=False),
        sa.Column("metrics", JSONType, nullable=True),
        sa.Column("gate", JSONType, nullable=True),
        sa.Column("gate_passed", sa.Boolean(), nullable=True),
        sa.Column("total", sa.Integer(), nullable=False),
        sa.Column("passed", sa.Integer(), nullable=False),
        sa.Column("pass_rate", sa.Float(), nullable=False),
        sa.Column("started_at", UTCDateTime(), nullable=False),
        sa.Column("finished_at", UTCDateTime(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_suite_runs")),
    )
    for column in SUITE_INDEXES:
        op.create_index(op.f(f"ix_suite_runs_{column}"), "suite_runs", [column])

    op.create_table(
        "case_results",
        sa.Column("id", BigIntPK, nullable=False),
        sa.Column("suite_id", sa.Uuid(), nullable=False),
        sa.Column("case_id", sa.String(64), nullable=False),
        sa.Column("case_hash", sa.String(80), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=True),
        sa.Column("passed", sa.Boolean(), nullable=False),
        sa.Column("status", sa.String(24), nullable=True),
        sa.Column("harness_error", sa.Text(), nullable=True),
        sa.Column("result", JSONType, nullable=False),
        sa.ForeignKeyConstraint(
            ["suite_id"],
            ["suite_runs.id"],
            name=op.f("fk_case_results_suite_id_suite_runs"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["run_id"], ["runs.id"], name=op.f("fk_case_results_run_id_runs"), ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_case_results")),
        sa.UniqueConstraint("suite_id", "case_id", name="uq_case_results_suite_id_case_id"),
    )
    op.create_index(op.f("ix_case_results_run_id"), "case_results", ["run_id"])

    op.create_table(
        "check_results",
        sa.Column("case_result_id", sa.BigInteger(), nullable=False),
        sa.Column("check_name", sa.String(32), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("reason", sa.String(48), nullable=False),
        sa.ForeignKeyConstraint(
            ["case_result_id"],
            ["case_results.id"],
            name=op.f("fk_check_results_case_result_id_case_results"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("case_result_id", "check_name", name=op.f("pk_check_results")),
    )

    op.create_table(
        "experiments",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("created_at", UTCDateTime(), nullable=False),
        sa.Column("dataset_name", sa.String(64), nullable=False),
        sa.Column("dataset_version", sa.String(64), nullable=False),
        sa.Column("dataset_hash", sa.String(80), nullable=False),
        sa.Column("suite_a_id", sa.Uuid(), nullable=False),
        sa.Column("suite_b_id", sa.Uuid(), nullable=False),
        sa.Column("comparison", JSONType, nullable=False),
        sa.Column("a_ref", sa.String(130), nullable=False),
        sa.Column("b_ref", sa.String(130), nullable=False),
        sa.Column("a_pass_rate", sa.Float(), nullable=False),
        sa.Column("b_pass_rate", sa.Float(), nullable=False),
        sa.Column("regressions", sa.Integer(), nullable=False),
        sa.Column("fixes", sa.Integer(), nullable=False),
        sa.Column("confounded", sa.Boolean(), nullable=False),
        sa.Column("deterministic", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(
            ["suite_a_id"],
            ["suite_runs.id"],
            name=op.f("fk_experiments_suite_a_id_suite_runs"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["suite_b_id"],
            ["suite_runs.id"],
            name=op.f("fk_experiments_suite_b_id_suite_runs"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_experiments")),
    )

    # 0001 had no suite_runs table, so any suite_id already stored points nowhere.
    op.execute(sa.text("UPDATE runs SET suite_id = NULL WHERE suite_id IS NOT NULL"))
    with op.batch_alter_table("runs") as batch:
        batch.create_foreign_key(
            op.f("fk_runs_suite_id_suite_runs"),
            "suite_runs",
            ["suite_id"],
            ["id"],
            ondelete="CASCADE",
        )


def downgrade() -> None:
    with op.batch_alter_table("runs") as batch:
        batch.drop_constraint(op.f("fk_runs_suite_id_suite_runs"), type_="foreignkey")
    op.drop_table("experiments")
    op.drop_table("check_results")
    op.drop_index(op.f("ix_case_results_run_id"), table_name="case_results")
    op.drop_table("case_results")
    for column in SUITE_INDEXES:
        op.drop_index(op.f(f"ix_suite_runs_{column}"), table_name="suite_runs")
    op.drop_table("suite_runs")
    op.drop_table("datasets")
