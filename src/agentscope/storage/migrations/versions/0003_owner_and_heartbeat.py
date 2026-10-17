"""Owner and heartbeat on suites and runs, so startup recovery marks only work whose process
is gone or silent, never another process's live suite.

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-16
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from agentscope.storage.types import UTCDateTime

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLES = ("suite_runs", "runs")


def upgrade() -> None:
    # Nullable columns: SQLite adds them in place (no table rebuild), and rows written before
    # this revision have no owner, so recovery falls back to their start time.
    for table in TABLES:
        op.add_column(table, sa.Column("owner", sa.String(300), nullable=True))
        op.add_column(table, sa.Column("heartbeat_at", UTCDateTime(), nullable=True))


def downgrade() -> None:
    for table in TABLES:
        with op.batch_alter_table(table) as batch:
            batch.drop_column("heartbeat_at")
            batch.drop_column("owner")
