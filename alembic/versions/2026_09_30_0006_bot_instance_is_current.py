"""Add is_current flag and partial unique index to bot_instances.

Revision ID: 2026_09_30_0006
Revises: 2026_09_30_0005
"""

from collections.abc import Sequence
from alembic import op
import sqlalchemy as sa


revision: str = "2026_09_30_0006"
down_revision: str | None = "2026_09_30_0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. Add is_current column
    op.add_column(
        "bot_instances",
        sa.Column("is_current", sa.Boolean(), server_default=sa.text("true"), nullable=False),
    )

    # 2. In case of any existing multiple records per master, keep only the latest as is_current=true
    op.execute(
        """
        UPDATE bot_instances
        SET is_current = FALSE
        WHERE id NOT IN (
            SELECT DISTINCT ON (master_id) id
            FROM bot_instances
            ORDER BY master_id, id DESC
        );
        """
    )

    # 3. Add partial unique index: at most one current bot instance per master
    op.execute(
        """
        CREATE UNIQUE INDEX uq_bot_instances_current_per_master
        ON bot_instances (master_id)
        WHERE is_current = TRUE;
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_bot_instances_current_per_master;")
    op.drop_column("bot_instances", "is_current")
