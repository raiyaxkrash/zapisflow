"""Manager onboarding foundation: AuditLog actor_user_id, nullable admin_id, and partial unique active bot constraint.

Revision ID: 2026_09_30_0005
Revises: 2026_09_30_0004
"""

from collections.abc import Sequence
from alembic import op
import sqlalchemy as sa


revision: str = "2026_09_30_0005"
down_revision: str | None = "2026_09_30_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. Update audit_logs table for platform/manager actions
    op.alter_column("audit_logs", "admin_id", existing_type=sa.Integer(), nullable=True)
    op.alter_column("audit_logs", "entity_id", existing_type=sa.Integer(), nullable=True)
    op.add_column(
        "audit_logs",
        sa.Column("actor_user_id", sa.BigInteger(), nullable=True),
    )
    op.create_foreign_key(
        "fk_audit_logs_actor_user_id_users",
        "audit_logs",
        "users",
        ["actor_user_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_audit_logs_actor_user_id",
        "audit_logs",
        ["actor_user_id"],
        unique=False,
    )

    # 2. Add partial unique index: exactly one ACTIVE bot instance per master
    op.execute(
        "CREATE UNIQUE INDEX uq_bot_instances_active_per_master "
        "ON bot_instances (master_id) "
        "WHERE status = 'ACTIVE';"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_bot_instances_active_per_master;")
    op.drop_index("ix_audit_logs_actor_user_id", table_name="audit_logs")
    op.drop_constraint("fk_audit_logs_actor_user_id_users", "audit_logs", type_="foreignkey")
    op.drop_column("audit_logs", "actor_user_id")
    op.alter_column("audit_logs", "entity_id", existing_type=sa.Integer(), nullable=False)
    op.alter_column("audit_logs", "admin_id", existing_type=sa.Integer(), nullable=False)
