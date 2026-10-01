"""Subscription plans v2, sort_order and promo codes schema.

Revision ID: 2026_10_01_0010
Revises: 2026_10_01_0009
"""

from collections.abc import Sequence
from alembic import op
import sqlalchemy as sa


revision: str = "2026_10_01_0010"
down_revision: str | None = "2026_10_01_0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. Add sort_order to subscription_plans
    op.add_column(
        "subscription_plans",
        sa.Column("sort_order", sa.Integer(), server_default="1", nullable=False),
    )

    # 2. Create promo_codes table
    op.create_table(
        "promo_codes",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("code", sa.String(length=32), nullable=False),
        sa.Column("discount_type", sa.String(length=16), server_default="PERCENT", nullable=False),
        sa.Column("discount_value", sa.Numeric(precision=10, scale=2), nullable=False),
        sa.Column("max_uses", sa.Integer(), nullable=True),
        sa.Column("used_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("valid_from", sa.DateTime(timezone=True), nullable=True),
        sa.Column("valid_until", sa.DateTime(timezone=True), nullable=True),
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
    op.create_index("ix_promo_codes_code", "promo_codes", ["code"], unique=True)

    # 3. Seed / update primary plan 'basic_monthly' and set sort_order
    op.execute(
        """
        INSERT INTO subscription_plans (code, name, description, price, currency, period_days, is_active, sort_order, created_at, updated_at)
        VALUES 
            ('basic_monthly', 'ZapisFlow Basic', 'Полный доступ к онлайн-записи и функциям платформы на 30 дней', 499.00, 'RUB', 30, true, 1, NOW(), NOW())
        ON CONFLICT (code) DO UPDATE SET 
            name = EXCLUDED.name,
            description = EXCLUDED.description,
            price = EXCLUDED.price,
            period_days = EXCLUDED.period_days,
            is_active = EXCLUDED.is_active,
            sort_order = EXCLUDED.sort_order,
            updated_at = NOW();
        """
    )

    # Update sort_order for multi-period plans and deactivate legacy 'BASIC'
    op.execute("UPDATE subscription_plans SET is_active = false, sort_order = 99 WHERE code = 'BASIC';")
    op.execute("UPDATE subscription_plans SET sort_order = 2 WHERE code = 'BASIC_3M';")
    op.execute("UPDATE subscription_plans SET sort_order = 3 WHERE code = 'BASIC_12M';")


def downgrade() -> None:
    # Rollback plans
    op.execute("UPDATE subscription_plans SET is_active = true, sort_order = 1 WHERE code = 'BASIC';")
    op.execute("DELETE FROM subscription_plans WHERE code = 'basic_monthly';")
    op.drop_index("ix_promo_codes_code", table_name="promo_codes")
    op.drop_table("promo_codes")
    op.drop_column("subscription_plans", "sort_order")
