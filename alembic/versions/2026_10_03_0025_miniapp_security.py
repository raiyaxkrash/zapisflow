"""Short-lived Mini App sessions and durable HTTP mutation idempotency.

No duplicated appointment/payment/CRM/schedule data.
"""

import sqlalchemy as sa

from alembic import op

revision = "2026_10_03_0025"
down_revision = "2026_10_03_0024"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "miniapp_sessions",
        sa.Column("token_hash", sa.String(64), primary_key=True),
        sa.Column("csrf_hash", sa.String(64), nullable=False),
        sa.Column("init_hash", sa.String(64), unique=True, nullable=False),
        sa.Column(
            "bot_instance_id",
            sa.BigInteger(),
            sa.ForeignKey("bot_instances.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("token_version", sa.Integer(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_miniapp_sessions_expires_at", "miniapp_sessions", ["expires_at"]
    )
    op.create_table(
        "miniapp_operations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "bot_instance_id",
            sa.BigInteger(),
            sa.ForeignKey("bot_instances.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("key", sa.String(64), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("result", sa.JSON(), nullable=False),
        sa.UniqueConstraint(
            "bot_instance_id", "user_id", "key", name="uq_miniapp_operation"
        ),
    )


def downgrade():
    op.drop_table("miniapp_operations")
    op.drop_table("miniapp_sessions")
