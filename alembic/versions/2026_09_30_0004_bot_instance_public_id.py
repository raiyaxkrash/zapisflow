"""Add public_id UUID column to bot_instances.

Revision ID: 2026_09_30_0004
Revises: 2026_09_30_0003
"""

from collections.abc import Sequence
from alembic import op
import sqlalchemy as sa


revision: str = "2026_09_30_0004"
down_revision: str | None = "2026_09_30_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. Add column as nullable first to allow backfill
    op.add_column(
        "bot_instances",
        sa.Column(
            "public_id",
            sa.Uuid(),
            server_default=sa.text("gen_random_uuid()"),
            nullable=True,
        ),
    )

    # 2. Backfill existing records with UUIDs if any have NULL
    op.execute("UPDATE bot_instances SET public_id = gen_random_uuid() WHERE public_id IS NULL")

    # 3. Alter column to NOT NULL
    op.alter_column("bot_instances", "public_id", nullable=False)

    # 4. Create unique index for fast lookups by public_id
    op.create_index(
        "ix_bot_instances_public_id",
        "bot_instances",
        ["public_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("ix_bot_instances_public_id", table_name="bot_instances")
    op.drop_column("bot_instances", "public_id")
