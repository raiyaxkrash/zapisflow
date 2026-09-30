"""Initial PostgreSQL database schema

Revision ID: 2026_09_30_0001
Revises: 
Create Date: 2026-09-30 14:20:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "2026_09_30_0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 0. Enable btree_gist extension for exclusion constraints
    op.execute("CREATE EXTENSION IF NOT EXISTS btree_gist;")

    # Enum types
    deposit_type_enum = sa.Enum("FIXED", "PERCENT", name="deposit_type_enum", native_enum=True)
    appointment_status_enum = sa.Enum(
        "WAITING_PAYMENT",
        "PAYMENT_PROOF_SENT",
        "CONFIRMED",
        "CANCELLED_BY_CLIENT",
        "CANCELLED_BY_ADMIN",
        "COMPLETED",
        "NO_SHOW",
        "EXPIRED",
        name="appointment_status_enum",
        native_enum=True,
    )
    payment_status_enum = sa.Enum(
        "PENDING",
        "SUBMITTED",
        "CONFIRMED",
        "REJECTED",
        "RETAINED",
        name="payment_status_enum",
        native_enum=True,
    )
    media_type_enum = sa.Enum("PHOTO", "DOCUMENT", name="media_type_enum", native_enum=True)
    broadcast_status_enum = sa.Enum(
        "DRAFT", "SENDING", "COMPLETED", "CANCELLED", name="broadcast_status_enum", native_enum=True
    )
    recipient_status_enum = sa.Enum(
        "PENDING", "SENT", "FAILED", name="recipient_status_enum", native_enum=True
    )
    notification_type_enum = sa.Enum(
        "REMINDER_24H", "REMINDER_3H", "CUSTOM", name="notification_type_enum", native_enum=True
    )
    notification_status_enum = sa.Enum(
        "PENDING", "SENT", "CANCELLED", "FAILED", name="notification_status_enum", native_enum=True
    )

    # 1. Users
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("telegram_id", sa.BigInteger(), nullable=False),
        sa.Column("username", sa.String(length=64), nullable=True),
        sa.Column("first_name", sa.String(length=128), nullable=False),
        sa.Column("last_name", sa.String(length=128), nullable=True),
        sa.Column("phone", sa.String(length=32), nullable=True),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("last_activity_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("admin_notes", sa.Text(), nullable=True),
        sa.Column("is_bot_blocked", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_users")),
    )
    op.create_index(op.f("ix_users_telegram_id"), "users", ["telegram_id"], unique=True)
    op.create_index(op.f("ix_users_username"), "users", ["username"], unique=False)
    op.create_index(op.f("ix_users_phone"), "users", ["phone"], unique=False)

    # 2. Admins
    op.create_table(
        "admins",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(length=32), server_default="ADMIN", nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name=op.f("fk_admins_user_id_users"), ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_admins")),
        sa.UniqueConstraint("user_id", name=op.f("uq_admins_user_id")),
    )

    # 3. User Marketing Preferences
    op.create_table(
        "user_marketing_preferences",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("is_marketing_allowed", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name=op.f("fk_user_marketing_preferences_user_id_users"), ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id", name=op.f("pk_user_marketing_preferences")),
    )

    # 4. Services
    op.create_table(
        "services",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("master_id", sa.Integer(), server_default="1", nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("price", sa.Numeric(precision=10, scale=2), nullable=False),
        sa.Column("duration_min", sa.Integer(), nullable=False),
        sa.Column("buffer_min", sa.Integer(), server_default="30", nullable=False),
        sa.Column("deposit_type", deposit_type_enum, server_default="FIXED", nullable=False),
        sa.Column("deposit_value", sa.Numeric(precision=10, scale=2), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("is_archived", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("display_order", sa.Integer(), server_default="0", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_services")),
    )
    op.create_index(op.f("ix_services_master_id"), "services", ["master_id"], unique=False)
    op.create_index(op.f("ix_services_is_active"), "services", ["is_active"], unique=False)
    op.create_index(op.f("ix_services_is_archived"), "services", ["is_archived"], unique=False)

    # 5. Schedule Templates
    op.create_table(
        "schedule_templates",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("master_id", sa.Integer(), server_default="1", nullable=False),
        sa.Column("day_of_week", sa.SmallInteger(), nullable=False),
        sa.Column("is_day_off", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("work_start", sa.Time(), server_default="10:00", nullable=False),
        sa.Column("work_end", sa.Time(), server_default="19:00", nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_schedule_templates")),
        sa.UniqueConstraint("master_id", "day_of_week", name="uq_master_weekday"),
    )
    op.create_index(op.f("ix_schedule_templates_master_id"), "schedule_templates", ["master_id"], unique=False)

    # 6. Schedule Template Breaks
    op.create_table(
        "schedule_template_breaks",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("template_id", sa.Integer(), nullable=False),
        sa.Column("break_start", sa.Time(), nullable=False),
        sa.Column("break_end", sa.Time(), nullable=False),
        sa.ForeignKeyConstraint(["template_id"], ["schedule_templates.id"], name=op.f("fk_schedule_template_breaks_template_id_schedule_templates"), ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_schedule_template_breaks")),
    )

    # 7. Schedule Exceptions
    op.create_table(
        "schedule_exceptions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("master_id", sa.Integer(), server_default="1", nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("is_day_off", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("work_start", sa.Time(), nullable=True),
        sa.Column("work_end", sa.Time(), nullable=True),
        sa.Column("comment", sa.String(length=255), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_schedule_exceptions")),
        sa.UniqueConstraint("master_id", "date", name="uq_master_date"),
    )
    op.create_index(op.f("ix_schedule_exceptions_master_id"), "schedule_exceptions", ["master_id"], unique=False)
    op.create_index(op.f("ix_schedule_exceptions_date"), "schedule_exceptions", ["date"], unique=False)

    # 8. Schedule Exception Breaks
    op.create_table(
        "schedule_exception_breaks",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("exception_id", sa.Integer(), nullable=False),
        sa.Column("break_start", sa.Time(), nullable=False),
        sa.Column("break_end", sa.Time(), nullable=False),
        sa.ForeignKeyConstraint(["exception_id"], ["schedule_exceptions.id"], name=op.f("fk_schedule_exception_breaks_exception_id_schedule_exceptions"), ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_schedule_exception_breaks")),
    )

    # 9. Blocked Intervals
    op.create_table(
        "blocked_intervals",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("master_id", sa.Integer(), server_default="1", nullable=False),
        sa.Column("start_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("end_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reason", sa.String(length=255), nullable=True),
        sa.Column("created_by_admin_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.ForeignKeyConstraint(["created_by_admin_id"], ["admins.id"], name=op.f("fk_blocked_intervals_created_by_admin_id_admins"), ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_blocked_intervals")),
    )
    op.create_index(op.f("ix_blocked_intervals_master_id"), "blocked_intervals", ["master_id"], unique=False)
    op.create_index(op.f("ix_blocked_intervals_start_time"), "blocked_intervals", ["start_time"], unique=False)
    op.create_index(op.f("ix_blocked_intervals_end_time"), "blocked_intervals", ["end_time"], unique=False)

    # 10. Appointments
    op.create_table(
        "appointments",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("master_id", sa.Integer(), server_default="1", nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("service_id", sa.Integer(), nullable=False),
        sa.Column("status", appointment_status_enum, server_default="WAITING_PAYMENT", nullable=False),
        sa.Column("start_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("end_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("end_time_with_buffer", sa.DateTime(timezone=True), nullable=False),
        sa.Column("hold_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancel_policy_agreed", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("cancel_reason", sa.Text(), nullable=True),
        sa.Column("snapshot_service_title", sa.String(length=255), nullable=False),
        sa.Column("snapshot_service_price", sa.Numeric(precision=10, scale=2), nullable=False),
        sa.Column("snapshot_service_duration_min", sa.Integer(), nullable=False),
        sa.Column("snapshot_buffer_duration_min", sa.Integer(), nullable=False),
        sa.Column("snapshot_deposit_amount", sa.Numeric(precision=10, scale=2), nullable=False),
        sa.Column("is_manual_by_admin", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("admin_notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.ForeignKeyConstraint(["service_id"], ["services.id"], name=op.f("fk_appointments_service_id_services"), ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name=op.f("fk_appointments_user_id_users"), ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_appointments")),
    )
    op.create_index(op.f("ix_appointments_master_id"), "appointments", ["master_id"], unique=False)
    op.create_index(op.f("ix_appointments_user_id"), "appointments", ["user_id"], unique=False)
    op.create_index(op.f("ix_appointments_service_id"), "appointments", ["service_id"], unique=False)
    op.create_index(op.f("ix_appointments_status"), "appointments", ["status"], unique=False)
    op.create_index(op.f("ix_appointments_start_time"), "appointments", ["start_time"], unique=False)
    op.create_index(op.f("ix_appointments_end_time_with_buffer"), "appointments", ["end_time_with_buffer"], unique=False)
    op.create_index(op.f("ix_appointments_hold_until"), "appointments", ["hold_until"], unique=False)

    # PostgreSQL EXCLUSION constraint on appointments using btree_gist
    op.execute("""
        ALTER TABLE appointments
        ADD CONSTRAINT no_overlapping_active_appointments
        EXCLUDE USING gist (
            master_id WITH =,
            tstzrange(start_time, end_time_with_buffer, '[)') WITH &&
        )
        WHERE (status IN ('CONFIRMED', 'PAYMENT_PROOF_SENT', 'WAITING_PAYMENT'));
    """)

    # 11. Payments
    op.create_table(
        "payments",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("appointment_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("amount", sa.Numeric(precision=10, scale=2), nullable=False),
        sa.Column("status", payment_status_enum, server_default="PENDING", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("confirmed_by_admin_id", sa.Integer(), nullable=True),
        sa.Column("rejection_reason", sa.String(length=255), nullable=True),
        sa.ForeignKeyConstraint(["appointment_id"], ["appointments.id"], name=op.f("fk_payments_appointment_id_appointments"), ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["confirmed_by_admin_id"], ["admins.id"], name=op.f("fk_payments_confirmed_by_admin_id_admins"), ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name=op.f("fk_payments_user_id_users"), ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_payments")),
    )
    op.create_index(op.f("ix_payments_appointment_id"), "payments", ["appointment_id"], unique=False)
    op.create_index(op.f("ix_payments_user_id"), "payments", ["user_id"], unique=False)
    op.create_index(op.f("ix_payments_status"), "payments", ["status"], unique=False)

    # 12. Payment Proofs
    op.create_table(
        "payment_proofs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("payment_id", sa.Integer(), nullable=False),
        sa.Column("telegram_file_id", sa.String(length=255), nullable=False),
        sa.Column("telegram_file_unique_id", sa.String(length=255), nullable=False),
        sa.Column("media_type", media_type_enum, server_default="PHOTO", nullable=False),
        sa.Column("user_comment", sa.Text(), nullable=True),
        sa.Column("uploaded_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.ForeignKeyConstraint(["payment_id"], ["payments.id"], name=op.f("fk_payment_proofs_payment_id_payments"), ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_payment_proofs")),
    )
    op.create_index(op.f("ix_payment_proofs_payment_id"), "payment_proofs", ["payment_id"], unique=False)

    # 13. Portfolio Categories
    op.create_table(
        "portfolio_categories",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=128), nullable=False),
        sa.Column("display_order", sa.Integer(), server_default="0", nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_portfolio_categories")),
    )

    # 14. Portfolio Items
    op.create_table(
        "portfolio_items",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("category_id", sa.Integer(), nullable=False),
        sa.Column("telegram_file_id", sa.String(length=255), nullable=False),
        sa.Column("telegram_file_unique_id", sa.String(length=255), nullable=False),
        sa.Column("caption", sa.Text(), nullable=True),
        sa.Column("display_order", sa.Integer(), server_default="0", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.ForeignKeyConstraint(["category_id"], ["portfolio_categories.id"], name=op.f("fk_portfolio_items_category_id_portfolio_categories"), ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_portfolio_items")),
    )
    op.create_index(op.f("ix_portfolio_items_category_id"), "portfolio_items", ["category_id"], unique=False)

    # 15. Settings
    op.create_table(
        "settings",
        sa.Column("key", sa.String(length=64), nullable=False),
        sa.Column("value", postgresql.JSONB(astext_type=sa.Text()) if op.get_bind().dialect.name == "postgresql" else sa.JSON(), nullable=False),
        sa.Column("description", sa.String(length=255), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.PrimaryKeyConstraint("key", name=op.f("pk_settings")),
    )

    # 16. Broadcasts
    op.create_table(
        "broadcasts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("admin_id", sa.Integer(), nullable=True),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("photo_file_id", sa.String(length=255), nullable=True),
        sa.Column("button_text", sa.String(length=64), nullable=True),
        sa.Column("button_url", sa.String(length=255), nullable=True),
        sa.Column("status", broadcast_status_enum, server_default="DRAFT", nullable=False),
        sa.Column("total_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("success_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("fail_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["admin_id"], ["admins.id"], name=op.f("fk_broadcasts_admin_id_admins"), ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_broadcasts")),
    )

    # 17. Broadcast Recipients
    op.create_table(
        "broadcast_recipients",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("broadcast_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("status", recipient_status_enum, server_default="PENDING", nullable=False),
        sa.Column("error_message", sa.String(length=255), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["broadcast_id"], ["broadcasts.id"], name=op.f("fk_broadcast_recipients_broadcast_id_broadcasts"), ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name=op.f("fk_broadcast_recipients_user_id_users"), ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_broadcast_recipients")),
    )
    op.create_index(op.f("ix_broadcast_recipients_broadcast_id"), "broadcast_recipients", ["broadcast_id"], unique=False)
    op.create_index(op.f("ix_broadcast_recipients_user_id"), "broadcast_recipients", ["user_id"], unique=False)

    # 18. Notifications
    op.create_table(
        "notifications",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("appointment_id", sa.Integer(), nullable=False),
        sa.Column("type", notification_type_enum, server_default="REMINDER_24H", nullable=False),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", notification_status_enum, server_default="PENDING", nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["appointment_id"], ["appointments.id"], name=op.f("fk_notifications_appointment_id_appointments"), ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_notifications")),
    )
    op.create_index(op.f("ix_notifications_appointment_id"), "notifications", ["appointment_id"], unique=False)
    op.create_index(op.f("ix_notifications_scheduled_at"), "notifications", ["scheduled_at"], unique=False)
    op.create_index(op.f("ix_notifications_status"), "notifications", ["status"], unique=False)

    # 19. Audit Logs
    op.create_table(
        "audit_logs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("admin_id", sa.Integer(), nullable=False),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("entity_type", sa.String(length=64), nullable=False),
        sa.Column("entity_id", sa.Integer(), nullable=False),
        sa.Column("payload_before", sa.JSON(), nullable=True),
        sa.Column("payload_after", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.ForeignKeyConstraint(["admin_id"], ["admins.id"], name=op.f("fk_audit_logs_admin_id_admins"), ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_audit_logs")),
    )
    op.create_index(op.f("ix_audit_logs_admin_id"), "audit_logs", ["admin_id"], unique=False)
    op.create_index(op.f("ix_audit_logs_action"), "audit_logs", ["action"], unique=False)
    op.create_index(op.f("ix_audit_logs_created_at"), "audit_logs", ["created_at"], unique=False)


def downgrade() -> None:
    op.drop_table("audit_logs")
    op.drop_table("notifications")
    op.drop_table("broadcast_recipients")
    op.drop_table("broadcasts")
    op.drop_table("settings")
    op.drop_table("portfolio_items")
    op.drop_table("portfolio_categories")
    op.drop_table("payment_proofs")
    op.drop_table("payments")

    # Drop exclusion constraint and appointments table
    op.execute("ALTER TABLE appointments DROP CONSTRAINT IF EXISTS no_overlapping_active_appointments;")
    op.drop_table("appointments")

    op.drop_table("blocked_intervals")
    op.drop_table("schedule_exception_breaks")
    op.drop_table("schedule_exceptions")
    op.drop_table("schedule_template_breaks")
    op.drop_table("schedule_templates")
    op.drop_table("services")
    op.drop_table("user_marketing_preferences")
    op.drop_table("admins")
    op.drop_table("users")

    # Drop custom PostgreSQL ENUM types
    for enum_name in (
        "notification_status_enum",
        "notification_type_enum",
        "recipient_status_enum",
        "broadcast_status_enum",
        "media_type_enum",
        "payment_status_enum",
        "appointment_status_enum",
        "deposit_type_enum",
    ):
        op.execute(f"DROP TYPE IF EXISTS {enum_name};")
