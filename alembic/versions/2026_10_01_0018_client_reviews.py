"""Add client reviews and ratings.

Revision ID: 2026_10_01_0018
Revises: 2026_10_01_0017
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "2026_10_01_0018"
down_revision: str | None = "2026_10_01_0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
    CREATE TABLE IF NOT EXISTS reviews (
        id BIGSERIAL PRIMARY KEY,
        master_id BIGINT NOT NULL REFERENCES masters(id) ON DELETE CASCADE,
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        appointment_id BIGINT NOT NULL REFERENCES appointments(id) ON DELETE CASCADE CONSTRAINT uq_reviews_appointment_id UNIQUE,
        rating INTEGER NOT NULL CONSTRAINT ck_reviews_rating_range CHECK (rating >= 1 AND rating <= 5),
        comment TEXT,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_reviews_master_created ON reviews (master_id, created_at)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_reviews_appointment_id ON reviews (appointment_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_reviews_user_id ON reviews (user_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_reviews_master_id ON reviews (master_id)")

    op.execute("ALTER TABLE master_settings ADD COLUMN IF NOT EXISTS vk_profile VARCHAR(128)")
    op.execute("ALTER TABLE broadcasts ADD COLUMN IF NOT EXISTS target_segment VARCHAR(32)")


def downgrade() -> None:
    op.execute("ALTER TABLE broadcasts DROP COLUMN IF EXISTS target_segment")
    op.execute("ALTER TABLE master_settings DROP COLUMN IF EXISTS vk_profile")
    op.execute("DROP TABLE IF EXISTS reviews CASCADE")


