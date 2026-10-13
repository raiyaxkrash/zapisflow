"""Add master activity type and portfolio item fields.

Revision ID: 2026_10_01_0019
Revises: 2026_10_01_0018
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "2026_10_01_0019"
down_revision: str | None = "2026_10_01_0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE masters ADD COLUMN IF NOT EXISTS activity_type VARCHAR(32)")
    op.execute("ALTER TABLE portfolio_items ADD COLUMN IF NOT EXISTS title VARCHAR(128)")
    op.execute(
        "ALTER TABLE portfolio_items ADD COLUMN IF NOT EXISTS service_id INTEGER REFERENCES services(id) ON DELETE SET NULL"
    )
    op.execute("ALTER TABLE portfolio_items ADD COLUMN IF NOT EXISTS is_active BOOLEAN NOT NULL DEFAULT TRUE")
    op.execute("CREATE INDEX IF NOT EXISTS ix_portfolio_items_service_id ON portfolio_items (service_id)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_portfolio_items_service_id")
    op.execute("ALTER TABLE portfolio_items DROP COLUMN IF EXISTS is_active")
    op.execute("ALTER TABLE portfolio_items DROP COLUMN IF EXISTS service_id")
    op.execute("ALTER TABLE portfolio_items DROP COLUMN IF EXISTS title")
    op.execute("ALTER TABLE masters DROP COLUMN IF EXISTS activity_type")
