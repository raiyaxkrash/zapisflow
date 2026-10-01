"""Persist completed webhook updates in the business transaction.

Revision ID: 2026_10_01_0013
Revises: 2026_10_01_0012
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "2026_10_01_0013"
down_revision: str | None = "2026_10_01_0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "processed_webhook_updates",
        sa.Column("scope", sa.String(length=64), nullable=False),
        sa.Column("update_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "processed_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("scope", "update_id", name="pk_processed_webhook_updates"),
    )


def downgrade() -> None:
    op.drop_table("processed_webhook_updates")
