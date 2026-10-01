"""Add structured master contact fields.

Revision ID: 2026_10_01_0015
Revises: 2026_10_01_0014
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "2026_10_01_0015"
down_revision: str | None = "2026_10_01_0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("master_settings", sa.Column("whatsapp_phone", sa.String(length=64), nullable=True))
    op.add_column("master_settings", sa.Column("working_hours_text", sa.Text(), nullable=True))
    op.add_column("master_settings", sa.Column("contacts_intro_text", sa.Text(), nullable=True))
    op.add_column("master_settings", sa.Column("telegram_username", sa.String(length=64), nullable=True))


def downgrade() -> None:
    op.drop_column("master_settings", "telegram_username")
    op.drop_column("master_settings", "contacts_intro_text")
    op.drop_column("master_settings", "working_hours_text")
    op.drop_column("master_settings", "whatsapp_phone")
