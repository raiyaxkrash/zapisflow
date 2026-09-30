"""Multi-tenant database schema foundation: Master, BotInstance, MasterClient, MasterSettings, MasterAdmin, tenant constraints and backfill.

Revision ID: 2026_09_30_0003
Revises: 2026_09_30_0002
"""

from collections.abc import Sequence
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "2026_09_30_0003"
down_revision: str | None = "2026_09_30_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. ENUM types (idempotent creation)
    op.execute("DO $$ BEGIN CREATE TYPE master_status_enum AS ENUM ('SETUP_REQUIRED', 'ACTIVE', 'SUSPENDED', 'ARCHIVED'); EXCEPTION WHEN duplicate_object THEN null; END $$;")
    op.execute("DO $$ BEGIN CREATE TYPE subscription_status_enum AS ENUM ('TRIAL', 'ACTIVE', 'EXPIRED', 'SUSPENDED'); EXCEPTION WHEN duplicate_object THEN null; END $$;")
    op.execute("DO $$ BEGIN CREATE TYPE bot_instance_status_enum AS ENUM ('PROVISIONING', 'SETUP_REQUIRED', 'ACTIVE', 'DISABLED', 'ERROR'); EXCEPTION WHEN duplicate_object THEN null; END $$;")
    op.execute("DO $$ BEGIN CREATE TYPE master_admin_role_enum AS ENUM ('OWNER', 'ADMIN'); EXCEPTION WHEN duplicate_object THEN null; END $$;")

    master_status_enum = postgresql.ENUM(
        "SETUP_REQUIRED", "ACTIVE", "SUSPENDED", "ARCHIVED", name="master_status_enum", create_type=False
    )
    subscription_status_enum = postgresql.ENUM(
        "TRIAL", "ACTIVE", "EXPIRED", "SUSPENDED", name="subscription_status_enum", create_type=False
    )
    bot_instance_status_enum = postgresql.ENUM(
        "PROVISIONING", "SETUP_REQUIRED", "ACTIVE", "DISABLED", "ERROR", name="bot_instance_status_enum", create_type=False
    )
    master_admin_role_enum = postgresql.ENUM(
        "OWNER", "ADMIN", name="master_admin_role_enum", create_type=False
    )

    # 2. Table: masters
    op.create_table(
        "masters",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("owner_user_id", sa.Integer(), nullable=False),
        sa.Column("display_name", sa.String(length=128), nullable=False),
        sa.Column(
            "status",
            master_status_enum,
            server_default="SETUP_REQUIRED",
            nullable=False,
        ),
        sa.Column(
            "subscription_status",
            subscription_status_enum,
            server_default="TRIAL",
            nullable=False,
        ),
        sa.Column("timezone", sa.String(length=64), server_default="Europe/Moscow", nullable=False),
        sa.Column("trial_ends_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("paid_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"], name="fk_masters_owner_user_id_users", ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_masters_owner_user_id", "masters", ["owner_user_id"])

    # 3. Table: bot_instances
    op.create_table(
        "bot_instances",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("master_id", sa.BigInteger(), nullable=False),
        sa.Column("telegram_bot_id", sa.BigInteger(), nullable=True),
        sa.Column("telegram_username", sa.String(length=128), nullable=True),
        sa.Column("telegram_first_name", sa.String(length=128), nullable=True),
        sa.Column("encrypted_token", sa.Text(), nullable=True),
        sa.Column("webhook_secret", sa.String(length=128), nullable=True),
        sa.Column(
            "status",
            bot_instance_status_enum,
            server_default="PROVISIONING",
            nullable=False,
        ),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("token_version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["master_id"], ["masters.id"], name="fk_bot_instances_master_id_masters", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("telegram_bot_id", name="uq_bot_instances_telegram_bot_id"),
    )
    op.create_index("idx_bot_instances_master_id", "bot_instances", ["master_id"])

    # 4. Table: master_settings
    op.create_table(
        "master_settings",
        sa.Column("master_id", sa.BigInteger(), nullable=False),
        sa.Column("bank_name", sa.String(length=128), nullable=True),
        sa.Column("bank_card_number", sa.String(length=64), nullable=True),
        sa.Column("bank_recipient_name", sa.String(length=128), nullable=True),
        sa.Column("studio_address", sa.Text(), nullable=True),
        sa.Column("studio_phone", sa.String(length=64), nullable=True),
        sa.Column("about_text", sa.Text(), nullable=True),
        sa.Column("hold_duration_minutes", sa.Integer(), server_default="30", nullable=False),
        sa.Column("cancel_policy_hours", sa.Integer(), server_default="24", nullable=False),
        sa.Column("booking_horizon_days", sa.Integer(), server_default="30", nullable=False),
        sa.Column("min_advance_hours", sa.Integer(), server_default="2", nullable=False),
        sa.Column("grid_step_minutes", sa.Integer(), server_default="30", nullable=False),
        sa.Column("default_buffer_minutes", sa.Integer(), server_default="15", nullable=False),
        sa.Column("reminder_24h_enabled", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("reminder_3h_enabled", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("hold_duration_minutes > 0", name="chk_master_settings_hold_positive"),
        sa.CheckConstraint("booking_horizon_days > 0", name="chk_master_settings_horizon_positive"),
        sa.CheckConstraint("min_advance_hours >= 0", name="chk_master_settings_advance_positive"),
        sa.CheckConstraint("grid_step_minutes > 0", name="chk_master_settings_grid_positive"),
        sa.CheckConstraint("default_buffer_minutes >= 0", name="chk_master_settings_buffer_positive"),
        sa.CheckConstraint("cancel_policy_hours >= 0", name="chk_master_settings_cancel_positive"),
        sa.ForeignKeyConstraint(["master_id"], ["masters.id"], name="fk_master_settings_master_id_masters", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("master_id"),
    )

    # 5. Table: master_clients
    op.create_table(
        "master_clients",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("master_id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("is_marketing_allowed", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("is_bot_blocked", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("first_visit_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_visit_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["master_id"], ["masters.id"], name="fk_master_clients_master_id_masters", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_master_clients_user_id_users", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("master_id", "user_id", name="uq_master_clients_master_user"),
    )
    op.create_index("idx_master_clients_master_id", "master_clients", ["master_id"])
    op.create_index("idx_master_clients_user_id", "master_clients", ["user_id"])

    # 6. Table: master_admins
    op.create_table(
        "master_admins",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("master_id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column(
            "role",
            master_admin_role_enum,
            server_default="ADMIN",
            nullable=False,
        ),
        sa.Column("is_active", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["master_id"], ["masters.id"], name="fk_master_admins_master_id_masters", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_master_admins_user_id_users", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("master_id", "user_id", name="uq_master_admins_master_user"),
    )
    op.create_index("idx_master_admins_master_id", "master_admins", ["master_id"])
    op.create_index("idx_master_admins_user_id", "master_admins", ["user_id"])

    # 7. BACKFILL MASTER #1
    # Ensure at least one owner user exists
    op.execute("""
        DO $$
        DECLARE
            v_owner_id INTEGER;
        BEGIN
            -- Try to find admin with role owner or any admin
            SELECT user_id INTO v_owner_id FROM admins WHERE upper(role) = 'OWNER' LIMIT 1;
            IF v_owner_id IS NULL THEN
                SELECT user_id INTO v_owner_id FROM admins LIMIT 1;
            END IF;
            IF v_owner_id IS NULL THEN
                SELECT id INTO v_owner_id FROM users ORDER BY id LIMIT 1;
            END IF;
            IF v_owner_id IS NULL THEN
                INSERT INTO users (id, telegram_id, first_name, username)
                VALUES (1, 0, 'Владелец системы', 'system_owner')
                ON CONFLICT DO NOTHING;
                v_owner_id := 1;
            END IF;

            -- Provision Master #1
            INSERT INTO masters (id, owner_user_id, display_name, status, subscription_status, timezone)
            VALUES (1, v_owner_id, 'Студия красоты', 'ACTIVE', 'ACTIVE', 'Europe/Moscow')
            ON CONFLICT (id) DO UPDATE SET owner_user_id = EXCLUDED.owner_user_id;

            -- Provision MasterSettings for Master #1 from legacy settings if available
            INSERT INTO master_settings (
                master_id, bank_name, bank_card_number, bank_recipient_name,
                studio_address, studio_phone, hold_duration_minutes, cancel_policy_hours
            )
            VALUES (
                1,
                (SELECT trim(both '"' from (value::jsonb->>'value')) FROM settings WHERE key = 'bank_name'),
                (SELECT trim(both '"' from (value::jsonb->>'value')) FROM settings WHERE key = 'bank_card_number'),
                (SELECT trim(both '"' from (value::jsonb->>'value')) FROM settings WHERE key = 'bank_recipient_name'),
                (SELECT trim(both '"' from (value::jsonb->>'value')) FROM settings WHERE key = 'studio_address'),
                (SELECT trim(both '"' from (value::jsonb->>'value')) FROM settings WHERE key = 'studio_phone'),
                COALESCE((SELECT NULLIF(regexp_replace(value::jsonb->>'value', '[^0-9]', '', 'g'), '')::int FROM settings WHERE key = 'hold_duration_minutes'), 30),
                COALESCE((SELECT NULLIF(regexp_replace(value::jsonb->>'value', '[^0-9]', '', 'g'), '')::int FROM settings WHERE key = 'cancel_policy_hours'), 24)
            ) ON CONFLICT (master_id) DO NOTHING;

            -- Backfill master_admins
            INSERT INTO master_admins (master_id, user_id, role, is_active, created_at)
            SELECT 1, a.user_id,
                   CASE WHEN upper(a.role) = 'OWNER' THEN 'OWNER'::master_admin_role_enum ELSE 'ADMIN'::master_admin_role_enum END,
                   a.is_active, a.created_at
            FROM admins a
            ON CONFLICT (master_id, user_id) DO NOTHING;

            -- Backfill master_clients and marketing opt-ins
            INSERT INTO master_clients (master_id, user_id, is_marketing_allowed, is_bot_blocked, created_at, updated_at)
            SELECT 1, u.id,
                   COALESCE(p.is_marketing_allowed, FALSE),
                   COALESCE(u.is_bot_blocked, FALSE),
                   u.first_seen_at, u.last_activity_at
            FROM users u
            LEFT JOIN user_marketing_preferences p ON p.user_id = u.id
            ON CONFLICT (master_id, user_id) DO NOTHING;

            -- Synchronize sequences for tables with explicit ID backfills
            PERFORM setval(pg_get_serial_sequence('users', 'id'), COALESCE((SELECT max(id) FROM users), 1));
            PERFORM setval(pg_get_serial_sequence('masters', 'id'), COALESCE((SELECT max(id) FROM masters), 1));
            PERFORM setval(pg_get_serial_sequence('master_clients', 'id'), COALESCE((SELECT max(id) FROM master_clients), 1));
        END $$;
    """)

    # 8. ALTER EXISTING TABLES AND ADD FKs / CONSTRAINTS

    # A. services
    op.execute("UPDATE services SET master_id = 1 WHERE master_id IS NULL OR master_id NOT IN (SELECT id FROM masters)")
    op.alter_column("services", "master_id", server_default=None)
    op.create_foreign_key(
        "fk_services_master_id_masters", "services", "masters", ["master_id"], ["id"], ondelete="RESTRICT"
    )
    op.create_check_constraint("chk_services_price_positive", "services", "price >= 0")
    op.create_check_constraint("chk_services_deposit_positive", "services", "deposit_value >= 0")
    op.create_check_constraint("chk_services_duration_positive", "services", "duration_min > 0")
    op.create_check_constraint("chk_services_buffer_positive", "services", "buffer_min >= 0")
    op.create_index("idx_services_master_active", "services", ["master_id", "is_active"])

    # B. appointments
    op.execute("UPDATE appointments SET master_id = 1 WHERE master_id IS NULL OR master_id NOT IN (SELECT id FROM masters)")
    op.alter_column("appointments", "master_id", server_default=None)
    op.create_foreign_key(
        "fk_appointments_master_id_masters", "appointments", "masters", ["master_id"], ["id"], ondelete="RESTRICT"
    )
    op.create_unique_constraint("uq_appointments_id_master_id", "appointments", ["id", "master_id"])
    op.create_check_constraint("chk_appointments_price_positive", "appointments", "snapshot_service_price >= 0")
    op.create_check_constraint("chk_appointments_deposit_positive", "appointments", "snapshot_deposit_amount >= 0")
    op.create_check_constraint("chk_appointments_duration_positive", "appointments", "snapshot_service_duration_min > 0")
    op.create_check_constraint("chk_appointments_buffer_positive", "appointments", "snapshot_buffer_duration_min >= 0")
    op.create_check_constraint("chk_appointments_start_before_end", "appointments", "start_time < end_time")
    op.create_check_constraint("chk_appointments_buffer_order", "appointments", "end_time <= end_time_with_buffer")
    op.create_index("idx_appointments_master_start", "appointments", ["master_id", "start_time"])
    op.create_index("idx_appointments_master_status", "appointments", ["master_id", "status"])

    # C. payments
    op.add_column("payments", sa.Column("master_id", sa.BigInteger(), nullable=True))
    op.execute("""
        UPDATE payments p
        SET master_id = a.master_id
        FROM appointments a
        WHERE p.appointment_id = a.id AND p.master_id IS NULL
    """)
    op.execute("UPDATE payments SET master_id = 1 WHERE master_id IS NULL OR master_id NOT IN (SELECT id FROM masters)")
    op.alter_column("payments", "master_id", nullable=False)
    op.create_foreign_key(
        "fk_payments_master_id_masters", "payments", "masters", ["master_id"], ["id"], ondelete="RESTRICT"
    )
    # Composite FK: (appointment_id, master_id) -> appointments(id, master_id)
    op.create_foreign_key(
        "fk_payments_appointment_master",
        "payments",
        "appointments",
        ["appointment_id", "master_id"],
        ["id", "master_id"],
        ondelete="RESTRICT",
    )
    op.create_check_constraint("chk_payments_amount_positive", "payments", "amount >= 0")
    op.create_index("idx_payments_master_status", "payments", ["master_id", "status"])

    # D. schedule_templates
    op.execute("UPDATE schedule_templates SET master_id = 1 WHERE master_id IS NULL OR master_id NOT IN (SELECT id FROM masters)")
    op.alter_column("schedule_templates", "master_id", server_default=None)
    op.create_foreign_key(
        "fk_schedule_templates_master_id_masters", "schedule_templates", "masters", ["master_id"], ["id"], ondelete="RESTRICT"
    )
    op.create_index("idx_schedule_templates_master_day", "schedule_templates", ["master_id", "day_of_week"])

    # E. schedule_exceptions
    op.execute("UPDATE schedule_exceptions SET master_id = 1 WHERE master_id IS NULL OR master_id NOT IN (SELECT id FROM masters)")
    op.alter_column("schedule_exceptions", "master_id", server_default=None)
    op.create_foreign_key(
        "fk_schedule_exceptions_master_id_masters", "schedule_exceptions", "masters", ["master_id"], ["id"], ondelete="RESTRICT"
    )
    op.create_index("idx_schedule_exceptions_master_date", "schedule_exceptions", ["master_id", "date"])

    # F. blocked_intervals
    op.execute("UPDATE blocked_intervals SET master_id = 1 WHERE master_id IS NULL OR master_id NOT IN (SELECT id FROM masters)")
    op.alter_column("blocked_intervals", "master_id", server_default=None)
    op.create_foreign_key(
        "fk_blocked_intervals_master_id_masters", "blocked_intervals", "masters", ["master_id"], ["id"], ondelete="RESTRICT"
    )
    op.create_check_constraint("chk_blocked_intervals_time_order", "blocked_intervals", "start_time < end_time")
    op.create_index("idx_blocked_intervals_master_start", "blocked_intervals", ["master_id", "start_time"])

    # G. portfolio_categories
    op.add_column("portfolio_categories", sa.Column("master_id", sa.BigInteger(), nullable=True))
    op.execute("UPDATE portfolio_categories SET master_id = 1 WHERE master_id IS NULL OR master_id NOT IN (SELECT id FROM masters)")
    op.alter_column("portfolio_categories", "master_id", nullable=False)
    op.create_foreign_key(
        "fk_portfolio_categories_master_id_masters", "portfolio_categories", "masters", ["master_id"], ["id"], ondelete="RESTRICT"
    )
    op.create_index("idx_portfolio_categories_master_active", "portfolio_categories", ["master_id", "is_active"])

    # H. broadcasts
    op.add_column("broadcasts", sa.Column("master_id", sa.BigInteger(), nullable=True))
    op.execute("UPDATE broadcasts SET master_id = 1 WHERE master_id IS NULL OR master_id NOT IN (SELECT id FROM masters)")
    op.alter_column("broadcasts", "master_id", nullable=False)
    op.create_foreign_key(
        "fk_broadcasts_master_id_masters", "broadcasts", "masters", ["master_id"], ["id"], ondelete="RESTRICT"
    )
    op.create_index("idx_broadcasts_master_status", "broadcasts", ["master_id", "status"])

    # I. audit_logs
    op.add_column("audit_logs", sa.Column("master_id", sa.BigInteger(), nullable=True))
    op.create_foreign_key(
        "fk_audit_logs_master_id_masters", "audit_logs", "masters", ["master_id"], ["id"], ondelete="SET NULL"
    )
    op.create_index("idx_audit_logs_master_created", "audit_logs", ["master_id", "created_at"])


def downgrade() -> None:
    # Reverse of upgrade

    # Audit logs
    op.drop_index("idx_audit_logs_master_created", table_name="audit_logs")
    op.drop_constraint("fk_audit_logs_master_id_masters", "audit_logs", type_="foreignkey")
    op.drop_column("audit_logs", "master_id")

    # Broadcasts
    op.drop_index("idx_broadcasts_master_status", table_name="broadcasts")
    op.drop_constraint("fk_broadcasts_master_id_masters", "broadcasts", type_="foreignkey")
    op.drop_column("broadcasts", "master_id")

    # Portfolio categories
    op.drop_index("idx_portfolio_categories_master_active", table_name="portfolio_categories")
    op.drop_constraint("fk_portfolio_categories_master_id_masters", "portfolio_categories", type_="foreignkey")
    op.drop_column("portfolio_categories", "master_id")

    # Blocked intervals
    op.drop_index("idx_blocked_intervals_master_start", table_name="blocked_intervals")
    op.drop_constraint("chk_blocked_intervals_time_order", "blocked_intervals", type_="check")
    op.drop_constraint("fk_blocked_intervals_master_id_masters", "blocked_intervals", type_="foreignkey")
    op.alter_column("blocked_intervals", "master_id", server_default="1")

    # Schedule exceptions
    op.drop_index("idx_schedule_exceptions_master_date", table_name="schedule_exceptions")
    op.drop_constraint("fk_schedule_exceptions_master_id_masters", "schedule_exceptions", type_="foreignkey")
    op.alter_column("schedule_exceptions", "master_id", server_default="1")

    # Schedule templates
    op.drop_index("idx_schedule_templates_master_day", table_name="schedule_templates")
    op.drop_constraint("fk_schedule_templates_master_id_masters", "schedule_templates", type_="foreignkey")
    op.alter_column("schedule_templates", "master_id", server_default="1")

    # Payments
    op.drop_index("idx_payments_master_status", table_name="payments")
    op.drop_constraint("chk_payments_amount_positive", "payments", type_="check")
    op.drop_constraint("fk_payments_appointment_master", "payments", type_="foreignkey")
    op.drop_constraint("fk_payments_master_id_masters", "payments", type_="foreignkey")
    op.drop_column("payments", "master_id")

    # Appointments
    op.drop_index("idx_appointments_master_status", table_name="appointments")
    op.drop_index("idx_appointments_master_start", table_name="appointments")
    op.drop_constraint("chk_appointments_buffer_order", "appointments", type_="check")
    op.drop_constraint("chk_appointments_start_before_end", "appointments", type_="check")
    op.drop_constraint("chk_appointments_buffer_positive", "appointments", type_="check")
    op.drop_constraint("chk_appointments_duration_positive", "appointments", type_="check")
    op.drop_constraint("chk_appointments_deposit_positive", "appointments", type_="check")
    op.drop_constraint("chk_appointments_price_positive", "appointments", type_="check")
    op.drop_constraint("uq_appointments_id_master_id", "appointments", type_="unique")
    op.drop_constraint("fk_appointments_master_id_masters", "appointments", type_="foreignkey")
    op.alter_column("appointments", "master_id", server_default="1")

    # Services
    op.drop_index("idx_services_master_active", table_name="services")
    op.drop_constraint("chk_services_buffer_positive", "services", type_="check")
    op.drop_constraint("chk_services_duration_positive", "services", type_="check")
    op.drop_constraint("chk_services_deposit_positive", "services", type_="check")
    op.drop_constraint("chk_services_price_positive", "services", type_="check")
    op.drop_constraint("fk_services_master_id_masters", "services", type_="foreignkey")
    op.alter_column("services", "master_id", server_default="1")

    # Tables
    op.drop_table("master_admins")
    op.drop_table("master_clients")
    op.drop_table("master_settings")
    op.drop_table("bot_instances")
    op.drop_table("masters")

    # ENUMs
    postgresql.ENUM(name="master_admin_role_enum").drop(op.get_bind(), checkfirst=True)
    postgresql.ENUM(name="bot_instance_status_enum").drop(op.get_bind(), checkfirst=True)
    postgresql.ENUM(name="subscription_status_enum").drop(op.get_bind(), checkfirst=True)
    postgresql.ENUM(name="master_status_enum").drop(op.get_bind(), checkfirst=True)
