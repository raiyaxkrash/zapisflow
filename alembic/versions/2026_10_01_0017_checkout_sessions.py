"""Add single-use, short-lived SaaS checkout sessions.

Revision ID: 2026_10_01_0017
Revises: 2026_10_01_0016
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "2026_10_01_0017"
down_revision: str | None = "2026_10_01_0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "checkout_sessions",
        sa.Column("id", sa.BigInteger(), autoincrement=True, primary_key=True),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("master_id", sa.BigInteger(), sa.ForeignKey("masters.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("payment_id", sa.BigInteger(), sa.ForeignKey("subscription_payments.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("plan_id", sa.BigInteger(), sa.ForeignKey("subscription_plans.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="ISSUED"),
        sa.Column("receipt_email", sa.String(length=254), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("status IN ('ISSUED', 'USED')", name="ck_checkout_session_status"),
        sa.UniqueConstraint("token_hash", name="uq_checkout_session_token_hash"),
        sa.UniqueConstraint("payment_id", name="uq_checkout_session_payment"),
    )
    op.create_index("ix_checkout_sessions_expires_at", "checkout_sessions", ["expires_at"])


def downgrade() -> None:
    op.drop_index("ix_checkout_sessions_expires_at", table_name="checkout_sessions")
    op.drop_table("checkout_sessions")
