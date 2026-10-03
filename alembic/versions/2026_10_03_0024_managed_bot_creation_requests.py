"""Create managed_bot_creation_requests table for durable bot-to-master correlation.

Revision ID: 2026_10_03_0024
Revises: 2026_10_03_0023
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "2026_10_03_0024"
down_revision: str | None = "2026_10_03_0023"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


status_enum = sa.Enum(
    "PENDING",
    "COMPLETED",
    "EXPIRED",
    "FAILED",
    name="managed_bot_request_status_enum",
)


def upgrade() -> None:
    status_enum.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "managed_bot_creation_requests",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "owner_user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("telegram_owner_user_id", sa.BigInteger(), nullable=False, index=True),
        sa.Column(
            "master_id",
            sa.BigInteger(),
            sa.ForeignKey("masters.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("request_id", sa.Integer(), server_default="1", nullable=False),
        sa.Column("suggested_name", sa.String(128), nullable=True),
        sa.Column("suggested_username", sa.String(64), nullable=True),
        sa.Column(
            "status",
            status_enum,
            server_default="PENDING",
            nullable=False,
            index=True,
        ),
        sa.Column("telegram_bot_id", sa.BigInteger(), nullable=True, index=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False, index=True),
    )

    op.create_index(
        "uq_pending_managed_bot_request_per_user",
        "managed_bot_creation_requests",
        ["telegram_owner_user_id"],
        unique=True,
        postgresql_where=sa.text("status = 'PENDING'"),
    )

    op.create_index(
        "ix_managed_bot_requests_owner_master",
        "managed_bot_creation_requests",
        ["owner_user_id", "master_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_managed_bot_requests_owner_master", table_name="managed_bot_creation_requests")
    op.drop_index("uq_pending_managed_bot_request_per_user", table_name="managed_bot_creation_requests")
    op.drop_table("managed_bot_creation_requests")
    status_enum.drop(op.get_bind(), checkfirst=True)
