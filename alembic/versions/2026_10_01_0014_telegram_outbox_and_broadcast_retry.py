"""Durable tenant Telegram outbox and broadcast retry timestamp.

Revision ID: 2026_10_01_0014
Revises: 2026_10_01_0013
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "2026_10_01_0014"
down_revision: str | None = "2026_10_01_0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "CREATE TYPE telegram_outbox_status_enum AS ENUM "
        "('PENDING', 'PROCESSING', 'SENT', 'FAILED')"
    )
    status_enum = postgresql.ENUM(name="telegram_outbox_status_enum", create_type=False)
    op.create_table(
        "telegram_outbox",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("master_id", sa.BigInteger(), nullable=False),
        sa.Column("bot_instance_id", sa.BigInteger(), nullable=False),
        sa.Column("operation_type", sa.String(length=32), nullable=False),
        sa.Column("target_chat_id", sa.BigInteger(), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=255), nullable=False),
        sa.Column("status", status_enum, nullable=False),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("claim_owner", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.String(length=255), nullable=True),
        sa.ForeignKeyConstraint(["master_id"], ["masters.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["bot_instance_id"], ["bot_instances.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("idempotency_key", name="uq_telegram_outbox_idempotency_key"),
    )
    op.create_index("ix_telegram_outbox_master_id", "telegram_outbox", ["master_id"])
    op.create_index("ix_telegram_outbox_bot_instance_id", "telegram_outbox", ["bot_instance_id"])
    op.create_index("ix_telegram_outbox_due", "telegram_outbox", ["status", "next_attempt_at"])
    op.create_index("ix_telegram_outbox_claim", "telegram_outbox", ["status", "claimed_at"])

    op.add_column(
        "broadcast_recipients",
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_broadcast_recipients_retry",
        "broadcast_recipients",
        ["status", "next_attempt_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_broadcast_recipients_retry", table_name="broadcast_recipients")
    op.drop_column("broadcast_recipients", "next_attempt_at")
    op.drop_index("ix_telegram_outbox_claim", table_name="telegram_outbox")
    op.drop_index("ix_telegram_outbox_due", table_name="telegram_outbox")
    op.drop_index("ix_telegram_outbox_bot_instance_id", table_name="telegram_outbox")
    op.drop_index("ix_telegram_outbox_master_id", table_name="telegram_outbox")
    op.drop_table("telegram_outbox")
    postgresql.ENUM(name="telegram_outbox_status_enum").drop(op.get_bind(), checkfirst=True)
