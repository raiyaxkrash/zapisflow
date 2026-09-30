"""Multi-replica reliable scheduler and job routing schema extensions.

Revision ID: 2026_10_01_0007
Revises: 2026_09_30_0006
"""

from collections.abc import Sequence
from alembic import op
import sqlalchemy as sa


revision: str = "2026_10_01_0007"
down_revision: str | None = "2026_09_30_0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. Extend enums with PROCESSING status
    op.execute("ALTER TYPE notification_status_enum ADD VALUE IF NOT EXISTS 'PROCESSING';")
    op.execute("ALTER TYPE recipient_status_enum ADD VALUE IF NOT EXISTS 'PROCESSING';")

    # 2. Add claim & retry columns to notifications
    op.add_column("notifications", sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("notifications", sa.Column("claimed_by", sa.String(64), nullable=True))
    op.add_column("notifications", sa.Column("attempt_count", sa.Integer(), server_default="0", nullable=False))
    op.add_column("notifications", sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("notifications", sa.Column("last_error", sa.Text(), nullable=True))

    # 3. Clean duplicates if any and create unique constraint on (appointment_id, type)
    op.execute(
        """
        DELETE FROM notifications
        WHERE id NOT IN (
            SELECT DISTINCT ON (appointment_id, type) id
            FROM notifications
            ORDER BY appointment_id, type, id DESC
        );
        """
    )
    op.create_unique_constraint(
        "uq_notifications_appointment_type",
        "notifications",
        ["appointment_id", "type"],
    )

    # 4. Performance indexes for worker claim, retry, and crash recovery
    op.create_index("ix_notifications_claim", "notifications", ["status", "scheduled_at"])
    op.create_index("ix_notifications_retry", "notifications", ["status", "next_attempt_at"])
    op.create_index("ix_notifications_recovery", "notifications", ["status", "claimed_at"])

    # 5. Add claim columns to broadcast_recipients
    op.add_column("broadcast_recipients", sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("broadcast_recipients", sa.Column("claimed_by", sa.String(64), nullable=True))
    op.add_column("broadcast_recipients", sa.Column("attempt_count", sa.Integer(), server_default="0", nullable=False))
    op.create_index("ix_broadcast_recipients_claim", "broadcast_recipients", ["broadcast_id", "status"])

    # 6. Index for hold cleaner job on appointments
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_appointments_hold_cleaner
        ON appointments (status, hold_until)
        WHERE status = 'WAITING_PAYMENT';
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_appointments_hold_cleaner;")
    op.drop_index("ix_broadcast_recipients_claim", table_name="broadcast_recipients")
    op.drop_column("broadcast_recipients", "attempt_count")
    op.drop_column("broadcast_recipients", "claimed_by")
    op.drop_column("broadcast_recipients", "claimed_at")

    op.drop_index("ix_notifications_recovery", table_name="notifications")
    op.drop_index("ix_notifications_retry", table_name="notifications")
    op.drop_index("ix_notifications_claim", table_name="notifications")
    op.drop_constraint("uq_notifications_appointment_type", "notifications", type_="unique")

    op.drop_column("notifications", "last_error")
    op.drop_column("notifications", "next_attempt_at")
    op.drop_column("notifications", "attempt_count")
    op.drop_column("notifications", "claimed_by")
    op.drop_column("notifications", "claimed_at")
