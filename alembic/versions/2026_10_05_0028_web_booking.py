"""Explicit opt-in website booking channel; existing tenants remain disabled."""
from alembic import op
import sqlalchemy as sa
revision = "2026_10_05_0028"
down_revision = "2026_10_04_0027"
branch_labels = None
depends_on = None

def upgrade():
    op.add_column("bot_instances", sa.Column("web_booking_enabled", sa.Boolean(), server_default=sa.false(), nullable=False))

def downgrade():
    op.drop_column("bot_instances", "web_booking_enabled")
