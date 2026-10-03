"""Add an opaque, unique checkout reference to subscription payments.

Revision ID: 2026_10_01_0016
Revises: 2026_10_01_0015

Historical payments retain a NULL reference. New checkout orders receive an
application-generated UUID, independent of database IDs and tenant IDs.
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "2026_10_01_0016"
down_revision: str | None = "2026_10_01_0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "subscription_payments",
        sa.Column("checkout_ref", sa.Uuid(as_uuid=True), nullable=True),
    )
    op.add_column(
        "subscription_payments",
        sa.Column("last_reconciled_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_unique_constraint(
        "uq_subscription_payment_checkout_ref",
        "subscription_payments",
        ["checkout_ref"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_subscription_payment_checkout_ref",
        "subscription_payments",
        type_="unique",
    )
    op.drop_column("subscription_payments", "checkout_ref")
    op.drop_column("subscription_payments", "last_reconciled_at")
