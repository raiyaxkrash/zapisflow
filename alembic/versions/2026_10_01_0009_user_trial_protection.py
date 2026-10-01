"""User trial abuse protection schema and backfill.

Revision ID: 2026_10_01_0009
Revises: 2026_10_01_0008
"""

from collections.abc import Sequence
from alembic import op
import sqlalchemy as sa


revision: str = "2026_10_01_0009"
down_revision: str | None = "2026_10_01_0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. Add trial_claimed_at and trial_ends_at to users table
    op.add_column(
        "users",
        sa.Column("trial_claimed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "users",
        sa.Column("trial_ends_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_users_trial_claimed_at", "users", ["trial_claimed_at"], unique=False)

    # 2. Backfill existing users who have masters created
    op.execute(
        """
        UPDATE users u
        SET trial_claimed_at = COALESCE(sub.earliest_trial, sub.min_created_at),
            trial_ends_at = sub.latest_trial_end
        FROM (
            SELECT owner_user_id,
                   MIN(created_at) AS min_created_at,
                   MIN(created_at) FILTER (WHERE subscription_status = 'TRIAL' OR trial_ends_at IS NOT NULL) AS earliest_trial,
                   MAX(trial_ends_at) AS latest_trial_end
            FROM masters
            GROUP BY owner_user_id
        ) sub
        WHERE u.id = sub.owner_user_id AND u.trial_claimed_at IS NULL;
        """
    )


def downgrade() -> None:
    op.drop_index("ix_users_trial_claimed_at", table_name="users")
    op.drop_column("users", "trial_ends_at")
    op.drop_column("users", "trial_claimed_at")
