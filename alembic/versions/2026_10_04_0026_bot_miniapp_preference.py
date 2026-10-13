"""Per-bot Mini App switch, preserving existing availability."""
import sqlalchemy as sa
from alembic import op
revision = "2026_10_04_0026"
down_revision = "2026_10_03_0025"
branch_labels = None
depends_on = None

def upgrade():
    op.add_column("bot_instances", sa.Column("mini_app_enabled", sa.Boolean(), nullable=False, server_default=sa.true()))

def downgrade():
    op.drop_column("bot_instances", "mini_app_enabled")
