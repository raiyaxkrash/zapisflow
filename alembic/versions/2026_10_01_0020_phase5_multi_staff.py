"""Phase 5: Multi-staff architecture with composite foreign keys and project-wide blocks.

Revision ID: 2026_10_01_0020
Revises: 2026_10_01_0019
"""

from collections.abc import Sequence
from alembic import op
import sqlalchemy as sa


revision: str = "2026_10_01_0020"
down_revision: str | None = "2026_10_01_0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. Create staff_members table
    op.execute("""
        CREATE TABLE IF NOT EXISTS staff_members (
            id BIGSERIAL PRIMARY KEY,
            master_id BIGINT NOT NULL REFERENCES masters(id) ON DELETE RESTRICT,
            user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
            display_name VARCHAR(128) NOT NULL,
            specialization VARCHAR(128),
            description TEXT,
            photo_file_id VARCHAR(255),
            is_active BOOLEAN NOT NULL DEFAULT TRUE,
            sort_order INTEGER NOT NULL DEFAULT 0,
            invite_token_hash VARCHAR(64),
            invite_expires_at TIMESTAMPTZ,
            invite_used_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            CONSTRAINT uq_staff_members_master_id_id UNIQUE (master_id, id)
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_staff_members_master_active ON staff_members (master_id, is_active)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_staff_members_user_id ON staff_members (user_id)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_staff_members_invite_hash ON staff_members (invite_token_hash)")

    # 2. Add composite UNIQUE on services (master_id, id)
    op.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint WHERE conname = 'uq_services_master_id_id'
            ) THEN
                ALTER TABLE services ADD CONSTRAINT uq_services_master_id_id UNIQUE (master_id, id);
            END IF;
        END $$;
    """)

    # 3. Add composite UNIQUE on appointments (master_id, id)
    op.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint WHERE conname = 'uq_appointments_master_id_id'
            ) THEN
                ALTER TABLE appointments ADD CONSTRAINT uq_appointments_master_id_id UNIQUE (master_id, id);
            END IF;
        END $$;
    """)

    # 4. Create staff_services table
    op.execute("""
        CREATE TABLE IF NOT EXISTS staff_services (
            id BIGSERIAL PRIMARY KEY,
            master_id BIGINT NOT NULL,
            staff_id BIGINT NOT NULL,
            service_id INTEGER NOT NULL,
            is_active BOOLEAN NOT NULL DEFAULT TRUE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            CONSTRAINT uq_staff_services_staff_service UNIQUE (staff_id, service_id),
            CONSTRAINT fk_staff_services_staff_master FOREIGN KEY (master_id, staff_id) REFERENCES staff_members(master_id, id) ON DELETE CASCADE,
            CONSTRAINT fk_staff_services_service_master FOREIGN KEY (master_id, service_id) REFERENCES services(master_id, id) ON DELETE CASCADE
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_staff_services_master_staff ON staff_services (master_id, staff_id)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_staff_services_master_service ON staff_services (master_id, service_id)")

    # 5. Expand master_admin_role_enum to include 'STAFF' and add staff_id to master_admins
    op.execute("""
        DO $$
        BEGIN
            ALTER TYPE master_admin_role_enum ADD VALUE IF NOT EXISTS 'STAFF';
        EXCEPTION
            WHEN duplicate_object THEN null;
        END $$;
    """)
    op.execute("ALTER TABLE master_admins ADD COLUMN IF NOT EXISTS staff_id BIGINT REFERENCES staff_members(id) ON DELETE CASCADE")
    op.execute("CREATE INDEX IF NOT EXISTS idx_master_admins_staff_id ON master_admins (staff_id)")

    # 6. Backfill Primary Staff for every existing Master
    op.execute("""
        INSERT INTO staff_members (master_id, user_id, display_name, specialization, is_active, sort_order)
        SELECT m.id, m.owner_user_id, m.display_name, COALESCE(m.activity_type, 'Основной мастер'), true, 0
        FROM masters m
        WHERE NOT EXISTS (
            SELECT 1 FROM staff_members sm WHERE sm.master_id = m.id
        )
    """)

    # 7. Associate existing services with primary staff in staff_services
    op.execute("""
        INSERT INTO staff_services (master_id, staff_id, service_id, is_active)
        SELECT s.master_id, sm.id, s.id, true
        FROM services s
        JOIN staff_members sm ON sm.master_id = s.master_id
        WHERE NOT EXISTS (
            SELECT 1 FROM staff_services ss WHERE ss.staff_id = sm.id AND ss.service_id = s.id
        )
    """)

    # 8. Appointments: add staff_id, backfill, NOT NULL, composite FKs and update exclusion constraint
    op.execute("ALTER TABLE appointments ADD COLUMN IF NOT EXISTS staff_id BIGINT")

    op.execute("""
        UPDATE appointments a
        SET staff_id = sm.id
        FROM staff_members sm
        WHERE a.master_id = sm.master_id AND a.staff_id IS NULL
    """)

    op.execute("ALTER TABLE appointments ALTER COLUMN staff_id SET NOT NULL")
    op.execute("CREATE INDEX IF NOT EXISTS idx_appointments_staff_start ON appointments (staff_id, start_time)")

    op.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_appointments_staff_master') THEN
                ALTER TABLE appointments ADD CONSTRAINT fk_appointments_staff_master
                FOREIGN KEY (master_id, staff_id) REFERENCES staff_members(master_id, id) ON DELETE RESTRICT;
            END IF;
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_appointments_service_master') THEN
                ALTER TABLE appointments ADD CONSTRAINT fk_appointments_service_master
                FOREIGN KEY (master_id, service_id) REFERENCES services(master_id, id) ON DELETE RESTRICT;
            END IF;
        END $$;
    """)

    op.execute("ALTER TABLE appointments DROP CONSTRAINT IF EXISTS no_overlapping_active_appointments")
    op.execute("""
        ALTER TABLE appointments ADD CONSTRAINT no_overlapping_active_appointments
        EXCLUDE USING gist (
            staff_id WITH =,
            tstzrange(start_time, end_time_with_buffer, '[)') WITH &&
        ) WHERE (status IN ('CONFIRMED', 'PAYMENT_PROOF_SENT', 'WAITING_PAYMENT'))
    """)

    # 9. Schedule templates: add staff_id, backfill, NOT NULL, update unique and composite FK
    op.execute("ALTER TABLE schedule_templates ADD COLUMN IF NOT EXISTS staff_id BIGINT")
    op.execute("""
        UPDATE schedule_templates st
        SET staff_id = sm.id
        FROM staff_members sm
        WHERE st.master_id = sm.master_id AND st.staff_id IS NULL
    """)
    op.execute("ALTER TABLE schedule_templates ALTER COLUMN staff_id SET NOT NULL")
    op.execute("ALTER TABLE schedule_templates DROP CONSTRAINT IF EXISTS uq_master_weekday")
    op.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'uq_staff_weekday') THEN
                ALTER TABLE schedule_templates ADD CONSTRAINT uq_staff_weekday UNIQUE (staff_id, day_of_week);
            END IF;
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_schedule_templates_staff_master') THEN
                ALTER TABLE schedule_templates ADD CONSTRAINT fk_schedule_templates_staff_master
                FOREIGN KEY (master_id, staff_id) REFERENCES staff_members(master_id, id) ON DELETE CASCADE;
            END IF;
        END $$;
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_schedule_templates_master_staff_day ON schedule_templates (master_id, staff_id, day_of_week)")

    # 10. Schedule exceptions: add nullable staff_id, keep project-wide as NULL, partial unique indexes
    op.execute("ALTER TABLE schedule_exceptions ADD COLUMN IF NOT EXISTS staff_id BIGINT")
    op.execute("ALTER TABLE schedule_exceptions DROP CONSTRAINT IF EXISTS uq_master_date")
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS uq_schedule_exceptions_project
        ON schedule_exceptions (master_id, date) WHERE staff_id IS NULL
    """)
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS uq_schedule_exceptions_staff
        ON schedule_exceptions (master_id, staff_id, date) WHERE staff_id IS NOT NULL
    """)
    op.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_schedule_exceptions_staff_master') THEN
                ALTER TABLE schedule_exceptions ADD CONSTRAINT fk_schedule_exceptions_staff_master
                FOREIGN KEY (master_id, staff_id) REFERENCES staff_members(master_id, id) ON DELETE CASCADE;
            END IF;
        END $$;
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_schedule_exceptions_staff_date ON schedule_exceptions (staff_id, date)")

    # 11. Blocked intervals: add nullable staff_id, keep project-wide as NULL
    op.execute("ALTER TABLE blocked_intervals ADD COLUMN IF NOT EXISTS staff_id BIGINT")
    op.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_blocked_intervals_staff_master') THEN
                ALTER TABLE blocked_intervals ADD CONSTRAINT fk_blocked_intervals_staff_master
                FOREIGN KEY (master_id, staff_id) REFERENCES staff_members(master_id, id) ON DELETE CASCADE;
            END IF;
        END $$;
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_blocked_intervals_staff_start ON blocked_intervals (staff_id, start_time)")

    # 12. Portfolio items: add master_id and staff_id, composite FK
    op.execute("ALTER TABLE portfolio_items ADD COLUMN IF NOT EXISTS master_id BIGINT")
    op.execute("ALTER TABLE portfolio_items ADD COLUMN IF NOT EXISTS staff_id BIGINT")
    op.execute("""
        UPDATE portfolio_items pi
        SET master_id = pc.master_id
        FROM portfolio_categories pc
        WHERE pi.category_id = pc.id AND pi.master_id IS NULL
    """)
    op.execute("ALTER TABLE portfolio_items ALTER COLUMN master_id SET NOT NULL")
    op.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_portfolio_items_master') THEN
                ALTER TABLE portfolio_items ADD CONSTRAINT fk_portfolio_items_master
                FOREIGN KEY (master_id) REFERENCES masters(id) ON DELETE CASCADE;
            END IF;
        END $$;
    """)
    op.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_portfolio_items_staff_master') THEN
                BEGIN
                    ALTER TABLE portfolio_items ADD CONSTRAINT fk_portfolio_items_staff_master
                    FOREIGN KEY (master_id, staff_id) REFERENCES staff_members(master_id, id) ON DELETE SET NULL (staff_id);
                EXCEPTION WHEN syntax_error OR feature_not_supported THEN
                    ALTER TABLE portfolio_items ADD CONSTRAINT fk_portfolio_items_staff_master
                    FOREIGN KEY (staff_id) REFERENCES staff_members(id) ON DELETE SET NULL;
                END;
            END IF;
        END $$;
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_portfolio_items_master_staff ON portfolio_items (master_id, staff_id)")

    # 13. Reviews: add staff_id and composite FK to appointments
    op.execute("ALTER TABLE reviews ADD COLUMN IF NOT EXISTS staff_id BIGINT")
    op.execute("""
        UPDATE reviews r
        SET staff_id = a.staff_id
        FROM appointments a
        WHERE r.appointment_id = a.id AND r.staff_id IS NULL
    """)
    op.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_reviews_appointment_master') THEN
                ALTER TABLE reviews ADD CONSTRAINT fk_reviews_appointment_master
                FOREIGN KEY (master_id, appointment_id) REFERENCES appointments(master_id, id) ON DELETE CASCADE;
            END IF;
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_reviews_staff') THEN
                ALTER TABLE reviews ADD CONSTRAINT fk_reviews_staff
                FOREIGN KEY (staff_id) REFERENCES staff_members(id) ON DELETE SET NULL;
            END IF;
        END $$;
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_reviews_master_staff ON reviews (master_id, staff_id)")


def downgrade() -> None:
    # 1. Reviews
    op.execute("ALTER TABLE reviews DROP CONSTRAINT IF EXISTS fk_reviews_staff")
    op.execute("ALTER TABLE reviews DROP CONSTRAINT IF EXISTS fk_reviews_appointment_master")
    op.execute("DROP INDEX IF EXISTS ix_reviews_master_staff")
    op.execute("ALTER TABLE reviews DROP COLUMN IF EXISTS staff_id")

    # 2. Portfolio items
    op.execute("ALTER TABLE portfolio_items DROP CONSTRAINT IF EXISTS fk_portfolio_items_staff_master")
    op.execute("ALTER TABLE portfolio_items DROP CONSTRAINT IF EXISTS fk_portfolio_items_master")
    op.execute("DROP INDEX IF EXISTS idx_portfolio_items_master_staff")
    op.execute("ALTER TABLE portfolio_items DROP COLUMN IF EXISTS staff_id")
    op.execute("ALTER TABLE portfolio_items DROP COLUMN IF EXISTS master_id")

    # 3. Blocked intervals
    op.execute("ALTER TABLE blocked_intervals DROP CONSTRAINT IF EXISTS fk_blocked_intervals_staff_master")
    op.execute("DROP INDEX IF EXISTS idx_blocked_intervals_staff_start")
    op.execute("ALTER TABLE blocked_intervals DROP COLUMN IF EXISTS staff_id")

    # 4. Schedule exceptions
    op.execute("ALTER TABLE schedule_exceptions DROP CONSTRAINT IF EXISTS fk_schedule_exceptions_staff_master")
    op.execute("DROP INDEX IF EXISTS idx_schedule_exceptions_staff_date")
    op.execute("DROP INDEX IF EXISTS uq_schedule_exceptions_project")
    op.execute("DROP INDEX IF EXISTS uq_schedule_exceptions_staff")
    op.execute("ALTER TABLE schedule_exceptions DROP COLUMN IF EXISTS staff_id")
    op.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'uq_master_date') THEN
                ALTER TABLE schedule_exceptions ADD CONSTRAINT uq_master_date UNIQUE (master_id, date);
            END IF;
        END $$;
    """)

    # 5. Schedule templates
    op.execute("ALTER TABLE schedule_templates DROP CONSTRAINT IF EXISTS fk_schedule_templates_staff_master")
    op.execute("ALTER TABLE schedule_templates DROP CONSTRAINT IF EXISTS uq_staff_weekday")
    op.execute("DROP INDEX IF EXISTS idx_schedule_templates_master_staff_day")
    op.execute("ALTER TABLE schedule_templates DROP COLUMN IF EXISTS staff_id")
    op.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'uq_master_weekday') THEN
                ALTER TABLE schedule_templates ADD CONSTRAINT uq_master_weekday UNIQUE (master_id, day_of_week);
            END IF;
        END $$;
    """)

    # 6. Appointments
    op.execute("ALTER TABLE appointments DROP CONSTRAINT IF EXISTS no_overlapping_active_appointments")
    op.execute("ALTER TABLE appointments DROP CONSTRAINT IF EXISTS fk_appointments_service_master")
    op.execute("ALTER TABLE appointments DROP CONSTRAINT IF EXISTS fk_appointments_staff_master")
    op.execute("DROP INDEX IF EXISTS idx_appointments_staff_start")
    op.execute("ALTER TABLE appointments DROP COLUMN IF EXISTS staff_id")
    op.execute("""
        ALTER TABLE appointments ADD CONSTRAINT no_overlapping_active_appointments
        EXCLUDE USING gist (
            master_id WITH =,
            tstzrange(start_time, end_time_with_buffer, '[)') WITH &&
        ) WHERE (status IN ('CONFIRMED', 'PAYMENT_PROOF_SENT', 'WAITING_PAYMENT'))
    """)

    # 7. Staff services & master admins
    op.execute("DROP TABLE IF EXISTS staff_services")
    op.execute("ALTER TABLE master_admins DROP COLUMN IF EXISTS staff_id")

    # 8. Staff members
    op.execute("DROP TABLE IF EXISTS staff_members")

    # 9. Composite UNIQUE on appointments and services
    op.execute("ALTER TABLE appointments DROP CONSTRAINT IF EXISTS uq_appointments_master_id_id")
    op.execute("ALTER TABLE services DROP CONSTRAINT IF EXISTS uq_services_master_id_id")

