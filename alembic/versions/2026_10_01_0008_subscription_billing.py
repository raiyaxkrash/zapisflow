"""SaaS subscription plans, billing periods and payment history schema.

Revision ID: 2026_10_01_0008
Revises: 2026_10_01_0007
"""

from collections.abc import Sequence
from alembic import op
import sqlalchemy as sa


revision: str = "2026_10_01_0008"
down_revision: str | None = "2026_10_01_0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. subscription_plans
    op.create_table(
        "subscription_plans",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("code", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("price", sa.Numeric(precision=10, scale=2), nullable=False),
        sa.Column("currency", sa.String(length=3), server_default="RUB", nullable=False),
        sa.Column("period_days", sa.Integer(), server_default="30", nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_subscription_plans_code", "subscription_plans", ["code"], unique=True)

    # 2. subscription_periods
    op.create_table(
        "subscription_periods",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "master_id",
            sa.BigInteger(),
            sa.ForeignKey("masters.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "plan_id",
            sa.BigInteger(),
            sa.ForeignKey("subscription_plans.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("status", sa.String(length=32), server_default="ACTIVE", nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("amount", sa.Numeric(precision=10, scale=2), nullable=True),
        sa.Column("currency", sa.String(length=3), server_default="RUB", nullable=False),
        sa.Column("external_payment_id", sa.String(length=128), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_subscription_periods_master_id", "subscription_periods", ["master_id"])
    op.create_index(
        "idx_sub_periods_master_starts", "subscription_periods", ["master_id", "starts_at"]
    )
    op.create_index(
        "idx_sub_periods_master_ends", "subscription_periods", ["master_id", "ends_at"]
    )

    # 3. subscription_payments
    op.create_table(
        "subscription_payments",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "master_id",
            sa.BigInteger(),
            sa.ForeignKey("masters.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "plan_id",
            sa.BigInteger(),
            sa.ForeignKey("subscription_plans.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("provider_payment_id", sa.String(length=128), nullable=False),
        sa.Column("amount", sa.Numeric(precision=10, scale=2), nullable=False),
        sa.Column("currency", sa.String(length=3), server_default="RUB", nullable=False),
        sa.Column("status", sa.String(length=32), server_default="PENDING", nullable=False),
        sa.Column("paid_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("period_days", sa.Integer(), server_default="30", nullable=False),
        sa.Column("metadata_json", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "provider", "provider_payment_id", name="uq_subscription_payment_provider_id"
        ),
    )
    op.create_index("ix_subscription_payments_master_id", "subscription_payments", ["master_id"])
    op.create_index(
        "ix_subscription_payments_provider_payment_id",
        "subscription_payments",
        ["provider_payment_id"],
    )
    op.create_index(
        "idx_sub_payments_master_created", "subscription_payments", ["master_id", "created_at"]
    )

    # 4. Seed default subscription plans (1M, 3M, 12M)
    op.execute(
        """
        INSERT INTO subscription_plans (code, name, description, price, currency, period_days, is_active, created_at, updated_at)
        VALUES 
            ('BASIC', '1 месяц', 'Полный доступ к онлайн-записи и функциям студии на 30 дней', 990.00, 'RUB', 30, true, NOW(), NOW()),
            ('BASIC_3M', '3 месяца', 'Полный доступ к онлайн-записи со скидкой (90 дней)', 2690.00, 'RUB', 90, true, NOW(), NOW()),
            ('BASIC_12M', '12 месяцев', 'Полный доступ к онлайн-записи со скидкой (365 дней)', 8990.00, 'RUB', 365, true, NOW(), NOW())
        ON CONFLICT (code) DO NOTHING;
        """
    )

    # 5. Backfill existing masters into subscription_periods
    op.execute(
        """
        INSERT INTO subscription_periods (master_id, plan_id, status, source, starts_at, ends_at, currency, created_at, updated_at)
        SELECT 
            m.id,
            (SELECT id FROM subscription_plans WHERE code = 'BASIC' LIMIT 1),
            CASE WHEN m.subscription_status = 'SUSPENDED' THEN 'SUSPENDED' ELSE 'ACTIVE' END,
            CASE WHEN m.subscription_status = 'TRIAL' THEN 'TRIAL' ELSE 'LEGACY' END,
            m.created_at,
            COALESCE(m.paid_until, m.trial_ends_at, m.created_at + INTERVAL '14 days'),
            'RUB',
            NOW(),
            NOW()
        FROM masters m
        WHERE NOT EXISTS (
            SELECT 1 FROM subscription_periods sp WHERE sp.master_id = m.id
        );
        """
    )


def downgrade() -> None:
    op.drop_table("subscription_payments")
    op.drop_table("subscription_periods")
    op.drop_table("subscription_plans")
