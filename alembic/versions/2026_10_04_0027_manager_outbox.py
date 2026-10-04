"""Allow durable Manager Bot subscription notifications in existing outbox."""
from alembic import op
import sqlalchemy as sa

revision = "2026_10_04_0027"
down_revision = "2026_10_04_0026"
branch_labels = None
depends_on = None


def upgrade():
    op.alter_column("telegram_outbox", "bot_instance_id", existing_type=sa.BigInteger(), nullable=True)


def downgrade():
    # Preserve financial notification history. Never delete queued deliveries.
    connection = op.get_bind()
    if connection.scalar(sa.text("SELECT EXISTS (SELECT 1 FROM telegram_outbox WHERE bot_instance_id IS NULL)")):
        raise RuntimeError("Manager outbox rows exist; cannot safely downgrade")
    op.alter_column("telegram_outbox", "bot_instance_id", existing_type=sa.BigInteger(), nullable=False)
