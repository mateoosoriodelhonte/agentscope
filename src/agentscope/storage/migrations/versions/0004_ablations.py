"""Ablations, and the link from an ablation's variant suites to it.

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-20
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from agentscope.storage.types import JSONType, UTCDateTime

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ablations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("created_at", UTCDateTime(), nullable=False),
        sa.Column("experiment_id", sa.Uuid(), nullable=True),
        sa.Column("dataset_name", sa.String(64), nullable=False),
        sa.Column("dataset_version", sa.String(64), nullable=False),
        sa.Column("dataset_hash", sa.String(80), nullable=False),
        sa.Column("a_suite_id", sa.Uuid(), nullable=False),
        sa.Column("b_suite_id", sa.Uuid(), nullable=False),
        sa.Column("a_ref", sa.String(130), nullable=False),
        sa.Column("b_ref", sa.String(130), nullable=False),
        sa.Column("deterministic", sa.Boolean(), nullable=False),
        sa.Column("total_effect_pp", sa.Float(), nullable=False),
        sa.Column("interaction_pp", sa.Float(), nullable=False),
        sa.Column("dimensions", JSONType, nullable=False),
        sa.Column("result", JSONType, nullable=False),
        sa.ForeignKeyConstraint(
            ["experiment_id"],
            ["experiments.id"],
            name=op.f("fk_ablations_experiment_id_experiments"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["a_suite_id"],
            ["suite_runs.id"],
            name=op.f("fk_ablations_a_suite_id_suite_runs"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["b_suite_id"],
            ["suite_runs.id"],
            name=op.f("fk_ablations_b_suite_id_suite_runs"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ablations")),
    )
    op.create_index(op.f("ix_ablations_experiment_id"), "ablations", ["experiment_id"])
    # Nullable and without a foreign key (a variant suite runs before its ablation is
    # stored), so SQLite adds the column in place, without rebuilding suite_runs.
    op.add_column("suite_runs", sa.Column("ablation_id", sa.Uuid(), nullable=True))
    op.create_index(op.f("ix_suite_runs_ablation_id"), "suite_runs", ["ablation_id"])


def downgrade() -> None:
    op.drop_index(op.f("ix_suite_runs_ablation_id"), table_name="suite_runs")
    with op.batch_alter_table("suite_runs") as batch:
        batch.drop_column("ablation_id")
    op.drop_index(op.f("ix_ablations_experiment_id"), table_name="ablations")
    op.drop_table("ablations")
