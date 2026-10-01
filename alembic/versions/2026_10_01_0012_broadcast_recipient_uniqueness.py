"""Keep legacy duplicate recipients as history and enforce one active delivery.

Revision ID: 2026_10_01_0012
Revises: 2026_10_01_0011
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "2026_10_01_0012"
down_revision: str | None = "2026_10_01_0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "broadcast_recipients",
        sa.Column("duplicate_of_id", sa.Integer(), nullable=True),
    )
    # Preserve every old row. Prefer a previously successful delivery as the
    # canonical one, so an old duplicate is never queued for another send.
    op.execute(
        """
        WITH ranked AS (
            SELECT id,
                   first_value(id) OVER (
                       PARTITION BY broadcast_id, user_id
                       ORDER BY CASE status::text
                           WHEN 'SENT' THEN 0
                           WHEN 'PROCESSING' THEN 1
                           WHEN 'PENDING' THEN 2
                           ELSE 3 END, id
                   ) AS canonical_id,
                   row_number() OVER (
                       PARTITION BY broadcast_id, user_id
                       ORDER BY CASE status::text
                           WHEN 'SENT' THEN 0
                           WHEN 'PROCESSING' THEN 1
                           WHEN 'PENDING' THEN 2
                           ELSE 3 END, id
                   ) AS position
            FROM broadcast_recipients
        )
        UPDATE broadcast_recipients AS br
           SET duplicate_of_id = ranked.canonical_id
          FROM ranked
         WHERE br.id = ranked.id AND ranked.position > 1
        """
    )
    op.create_index(
        "uq_broadcast_recipients_active_campaign_user",
        "broadcast_recipients",
        ["broadcast_id", "user_id"],
        unique=True,
        postgresql_where=sa.text("duplicate_of_id IS NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_broadcast_recipients_active_campaign_user",
        table_name="broadcast_recipients",
    )
    op.drop_column("broadcast_recipients", "duplicate_of_id")
