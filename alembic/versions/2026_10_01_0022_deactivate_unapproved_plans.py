"""Deactivate unapproved multi-month subscription plans.

Revision ID: 2026_10_01_0022
Revises: 2026_10_01_0021
"""

from collections.abc import Sequence

from alembic import op


revision: str = "2026_10_01_0022"
down_revision: str | None = "2026_10_01_0021"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Retain plans and all historical payments. Only remove purchase access.
    op.execute("""
        UPDATE subscription_plans
        SET is_active = false, updated_at = NOW()
        WHERE code IN ('basic_3_months', 'basic_6_months', 'basic_yearly')
          AND is_active = true
    """)


def downgrade() -> None:
    # The owner must explicitly approve reactivation; a rollback must not
    # silently put unapproved tariffs back on sale.
    pass
