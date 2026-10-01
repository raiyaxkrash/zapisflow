"""Link one subscription period to at most one billing payment.

Revision ID: 2026_10_01_0011
Revises: 2026_10_01_0010

Existing duplicate payment periods are retained as historical records. Only the
oldest unambiguous period is linked to its payment; no financial row is deleted.
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "2026_10_01_0011"
down_revision: str | None = "2026_10_01_0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "subscription_periods",
        sa.Column("subscription_payment_id", sa.BigInteger(), nullable=True),
    )
    op.create_foreign_key(
        "fk_subscription_period_payment",
        "subscription_periods",
        "subscription_payments",
        ["subscription_payment_id"],
        ["id"],
        ondelete="RESTRICT",
    )

    # Legacy periods have only an external ID without provider. Link a period
    # only if its master/external ID identifies exactly one payment, and select
    # the earliest period when the historic double-application bug made copies.
    op.execute(
        """
        WITH payment_matches AS (
            SELECT sp.id AS period_id, p.id AS payment_id,
                   count(*) OVER (PARTITION BY sp.id) AS payment_match_count,
                   row_number() OVER (
                       PARTITION BY p.id ORDER BY sp.created_at, sp.id
                   ) AS period_rank
            FROM subscription_periods AS sp
            JOIN subscription_payments AS p
              ON p.master_id = sp.master_id
             AND p.provider_payment_id = sp.external_payment_id
            WHERE sp.source = 'PAYMENT'
        )
        UPDATE subscription_periods AS sp
           SET subscription_payment_id = pm.payment_id
          FROM payment_matches AS pm
         WHERE sp.id = pm.period_id
           AND pm.payment_match_count = 1
           AND pm.period_rank = 1
        """
    )
    op.create_unique_constraint(
        "uq_subscription_period_payment",
        "subscription_periods",
        ["subscription_payment_id"],
    )


def downgrade() -> None:
    op.drop_constraint("uq_subscription_period_payment", "subscription_periods", type_="unique")
    op.drop_constraint("fk_subscription_period_payment", "subscription_periods", type_="foreignkey")
    op.drop_column("subscription_periods", "subscription_payment_id")
