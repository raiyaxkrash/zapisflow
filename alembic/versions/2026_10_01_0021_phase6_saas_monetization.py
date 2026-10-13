"""Phase 6: SaaS monetization, subscription plan features and multi-period plans.

Revision ID: 2026_10_01_0021
Revises: 2026_10_01_0020
"""

from collections.abc import Sequence
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "2026_10_01_0021"
down_revision: str | None = "2026_10_01_0020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. Add features JSONB column to subscription_plans
    op.add_column(
        "subscription_plans",
        sa.Column(
            "features",
            postgresql.JSONB(astext_type=sa.Text()).with_variant(sa.JSON(), "sqlite"),
            nullable=True,
        ),
    )

    # 2. Upsert canonical Phase 6 plans
    op.execute(
        """
        INSERT INTO subscription_plans (code, name, description, price, currency, period_days, is_active, sort_order, features, created_at, updated_at)
        VALUES 
            (
                'basic_monthly',
                'ZapisFlow Basic',
                'Полный доступ к онлайн-записи, CRM, расписанию, сотрудникам и функциям платформы на 30 дней',
                499.00,
                'RUB',
                30,
                true,
                1,
                '{"max_staff": null, "can_broadcast": true, "can_create_appointments": true}'::jsonb,
                NOW(),
                NOW()
            ),
            (
                'basic_3_months',
                'ZapisFlow 3 Месяца',
                'Полный доступ к платформе на 90 дней со скидкой (1 299 ₽)',
                1299.00,
                'RUB',
                90,
                true,
                2,
                '{"max_staff": null, "can_broadcast": true, "can_create_appointments": true}'::jsonb,
                NOW(),
                NOW()
            ),
            (
                'basic_6_months',
                'ZapisFlow 6 Месяцев',
                'Полный доступ к платформе на 180 дней со скидкой (2 390 ₽)',
                2390.00,
                'RUB',
                180,
                true,
                3,
                '{"max_staff": null, "can_broadcast": true, "can_create_appointments": true}'::jsonb,
                NOW(),
                NOW()
            ),
            (
                'basic_yearly',
                'ZapisFlow 1 Год',
                'Максимальная выгода: полный доступ к платформе на 365 дней (4 490 ₽)',
                4490.00,
                'RUB',
                365,
                true,
                4,
                '{"max_staff": null, "can_broadcast": true, "can_create_appointments": true}'::jsonb,
                NOW(),
                NOW()
            )
        ON CONFLICT (code) DO UPDATE SET 
            name = EXCLUDED.name,
            description = EXCLUDED.description,
            price = EXCLUDED.price,
            period_days = EXCLUDED.period_days,
            is_active = EXCLUDED.is_active,
            sort_order = EXCLUDED.sort_order,
            features = EXCLUDED.features,
            updated_at = NOW();
        """
    )

    # Deactivate legacy plan codes so only canonical Phase 6 plans are visible
    op.execute("UPDATE subscription_plans SET is_active = false, sort_order = 99 WHERE code IN ('BASIC', 'BASIC_3M', 'BASIC_12M');")


def downgrade() -> None:
    op.execute("DELETE FROM subscription_plans WHERE code IN ('basic_3_months', 'basic_6_months', 'basic_yearly');")
    op.drop_column("subscription_plans", "features")
