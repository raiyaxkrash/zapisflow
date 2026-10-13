"""Add managed bots support columns to bot_instances.

Revision ID: 2026_10_03_0023
Revises: 2026_10_01_0022
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "2026_10_03_0023"
down_revision: str | None = "2026_10_01_0022"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "bot_instances",
        sa.Column(
            "provisioning_source",
            sa.String(32),
            server_default=sa.text("'manual_token'"),
            nullable=False,
        ),
    )
    op.add_column(
        "bot_instances",
        sa.Column(
            "managed_by_platform",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
    )
    op.add_column(
        "bot_instances",
        sa.Column(
            "telegram_owner_user_id",
            sa.BigInteger(),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_bot_instances_telegram_owner_user_id",
        "bot_instances",
        ["telegram_owner_user_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_bot_instances_telegram_owner_user_id", table_name="bot_instances")
    op.drop_column("bot_instances", "telegram_owner_user_id")
    op.drop_column("bot_instances", "managed_by_platform")
    op.drop_column("bot_instances", "provisioning_source")
