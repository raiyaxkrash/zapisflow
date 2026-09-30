"""
ETL script for migrating legacy SQLite backup data to PostgreSQL 16 (Multi-Tenant Phase 2).
Supports --dry-run and --execute. Safe and idempotent.
"""

import argparse
import asyncio
import json
import logging
import os
import sqlite3
import sys
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional
import asyncpg

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("etl_sqlite_to_postgres")


def parse_datetime(val: Optional[str]) -> Optional[datetime]:
    if not val:
        return None
    # ISO or SQLite format
    for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(val, fmt)
            return dt.replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    try:
        dt = datetime.fromisoformat(val)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def parse_time(val: Any) -> Optional[Any]:
    if not val:
        return None
    if isinstance(val, str):
        from datetime import time as dt_time
        return dt_time.fromisoformat(val)
    return val


def extract_sqlite_data(sqlite_path: str) -> Dict[str, List[Dict[str, Any]]]:
    if not os.path.exists(sqlite_path):
        raise FileNotFoundError(f"SQLite file not found: {sqlite_path}")

    con = sqlite3.connect(sqlite_path)
    con.row_factory = sqlite3.Row
    cur = con.cursor()

    tables = [
        "users",
        "admins",
        "user_marketing_preferences",
        "settings",
        "services",
        "schedule_templates",
        "schedule_template_breaks",
        "portfolio_categories",
        "portfolio_items",
        "appointments",
        "payments",
        "payment_proofs",
    ]

    data = {}
    for table in tables:
        try:
            rows = [dict(r) for r in cur.execute(f'SELECT * FROM "{table}"').fetchall()]
            data[table] = rows
        except sqlite3.OperationalError:
            data[table] = []

    con.close()
    return data


async def run_etl(sqlite_path: str, pg_url: str, execute: bool = False) -> Dict[str, Any]:
    pg_dsn = pg_url.replace("postgresql+asyncpg://", "postgresql://")
    logger.info("Extracting SQLite backup data from: %s", sqlite_path)
    sqlite_data = extract_sqlite_data(sqlite_path)

    logger.info("Connecting to target PostgreSQL: %s", pg_dsn.split("@")[-1])
    conn = await asyncpg.connect(pg_dsn)

    report: Dict[str, Any] = {
        "mode": "EXECUTE" if execute else "DRY-RUN",
        "sqlite_counts": {t: len(rows) for t, rows in sqlite_data.items()},
        "postgres_counts_before": {},
        "postgres_counts_after": {},
        "fk_checks": {},
    }

    # Record counts before
    for t in sqlite_data.keys():
        try:
            c = await conn.fetchval(f'SELECT COUNT(*) FROM "{t}"')
            report["postgres_counts_before"][t] = c
        except Exception:
            report["postgres_counts_before"][t] = None

    if not execute:
        logger.info("=== DRY-RUN MODE: No database changes will be committed ===")
        for t, count in report["sqlite_counts"].items():
            logger.info("  SQLite '%s': %d records ready for import", t, count)
        await conn.close()
        return report

    # EXECUTE MODE inside transaction
    async with conn.transaction():
        # 1. Users
        for u in sqlite_data.get("users", []):
            await conn.execute("""
                INSERT INTO users (id, telegram_id, username, first_name, last_name, phone, first_seen_at, last_activity_at, admin_notes, is_bot_blocked)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
                ON CONFLICT (id) DO UPDATE SET
                    telegram_id = EXCLUDED.telegram_id,
                    username = EXCLUDED.username,
                    first_name = EXCLUDED.first_name,
                    phone = EXCLUDED.phone
            """, u["id"], u["telegram_id"], u["username"], u["first_name"], u["last_name"], u["phone"],
               parse_datetime(u.get("first_seen_at")), parse_datetime(u.get("last_activity_at")),
               u.get("admin_notes"), bool(u.get("is_bot_blocked", 0)))

        # 2. Master #1
        owner_id = 1
        admin_rows = sqlite_data.get("admins", [])
        if admin_rows:
            owner_id = admin_rows[0]["user_id"]
        await conn.execute("""
            INSERT INTO masters (id, owner_user_id, display_name, status, subscription_status, timezone)
            VALUES (1, $1, 'Студия красоты', 'ACTIVE', 'ACTIVE', 'Europe/Moscow')
            ON CONFLICT (id) DO UPDATE SET owner_user_id = EXCLUDED.owner_user_id
        """, owner_id)

        # 3. MasterSettings & Settings
        settings_rows = sqlite_data.get("settings", [])
        settings_dict = {}
        for s in settings_rows:
            raw_val = s.get("value", "")
            try:
                parsed = json.loads(raw_val)
                settings_dict[s["key"]] = parsed.get("value") if isinstance(parsed, dict) else parsed
            except Exception:
                settings_dict[s["key"]] = raw_val

            # Also ensure settings table row exists
            await conn.execute("""
                INSERT INTO settings (key, value, description, updated_at)
                VALUES ($1, $2, $3, $4)
                ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value
            """, s["key"], s["value"], s.get("description"), parse_datetime(s.get("updated_at")))

        await conn.execute("""
            INSERT INTO master_settings (
                master_id, bank_name, bank_card_number, bank_recipient_name,
                studio_address, studio_phone, hold_duration_minutes, cancel_policy_hours
            ) VALUES (1, $1, $2, $3, $4, $5, $6, $7)
            ON CONFLICT (master_id) DO UPDATE SET
                bank_name = EXCLUDED.bank_name,
                bank_card_number = EXCLUDED.bank_card_number,
                bank_recipient_name = EXCLUDED.bank_recipient_name,
                studio_address = EXCLUDED.studio_address,
                studio_phone = EXCLUDED.studio_phone
        """, settings_dict.get("bank_name"), settings_dict.get("bank_card_number"),
           settings_dict.get("bank_recipient_name"), settings_dict.get("studio_address"),
           settings_dict.get("studio_phone"),
           int(settings_dict.get("hold_duration_minutes") or 30),
           int(settings_dict.get("cancel_policy_hours") or 24))

        # 4. Admins & MasterAdmins
        for a in admin_rows:
            role = "OWNER" if a.get("role", "").lower() == "owner" else "ADMIN"
            await conn.execute("""
                INSERT INTO admins (id, user_id, role, is_active, created_at)
                VALUES ($1, $2, $3, $4, $5)
                ON CONFLICT (id) DO UPDATE SET role = EXCLUDED.role, is_active = EXCLUDED.is_active
            """, a["id"], a["user_id"], a.get("role", "ADMIN"), bool(a.get("is_active", 1)), parse_datetime(a.get("created_at")))

            await conn.execute("""
                INSERT INTO master_admins (master_id, user_id, role, is_active, created_at)
                VALUES (1, $1, $2, $3, $4)
                ON CONFLICT (master_id, user_id) DO UPDATE SET role = EXCLUDED.role, is_active = EXCLUDED.is_active
            """, a["user_id"], role, bool(a.get("is_active", 1)), parse_datetime(a.get("created_at")))

        # 5. User Marketing Preferences & MasterClients
        mktg_map = {m["user_id"]: bool(m.get("is_marketing_allowed", 0)) for m in sqlite_data.get("user_marketing_preferences", [])}
        for u in sqlite_data.get("users", []):
            allowed = mktg_map.get(u["id"], False)
            await conn.execute("""
                INSERT INTO master_clients (master_id, user_id, is_marketing_allowed, is_bot_blocked, created_at, updated_at)
                VALUES (1, $1, $2, $3, $4, $5)
                ON CONFLICT (master_id, user_id) DO UPDATE SET
                    is_marketing_allowed = EXCLUDED.is_marketing_allowed,
                    is_bot_blocked = EXCLUDED.is_bot_blocked
            """, u["id"], allowed, bool(u.get("is_bot_blocked", 0)),
               parse_datetime(u.get("first_seen_at")), parse_datetime(u.get("last_activity_at")))

        # 6. Services
        for s in sqlite_data.get("services", []):
            await conn.execute("""
                INSERT INTO services (id, master_id, title, description, price, duration_min, buffer_min, deposit_type, deposit_value, is_active, is_archived, display_order, created_at, updated_at)
                VALUES ($1, 1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13)
                ON CONFLICT (id) DO UPDATE SET
                    title = EXCLUDED.title,
                    price = EXCLUDED.price,
                    deposit_value = EXCLUDED.deposit_value,
                    duration_min = EXCLUDED.duration_min,
                    buffer_min = EXCLUDED.buffer_min
            """, s["id"], s["title"], s.get("description"), Decimal(str(s["price"])), s["duration_min"],
               s["buffer_min"], s["deposit_type"], Decimal(str(s["deposit_value"])),
               bool(s.get("is_active", 1)), bool(s.get("is_archived", 0)), s.get("display_order", 0),
               parse_datetime(s.get("created_at")), parse_datetime(s.get("updated_at")))

        # 7. Schedule Templates & Breaks
        for st in sqlite_data.get("schedule_templates", []):
            ws = parse_time(st["work_start"])
            we = parse_time(st["work_end"])
            await conn.execute("""
                INSERT INTO schedule_templates (id, master_id, day_of_week, is_day_off, work_start, work_end)
                VALUES ($1, 1, $2, $3, $4, $5)
                ON CONFLICT (id) DO UPDATE SET
                    is_day_off = EXCLUDED.is_day_off,
                    work_start = EXCLUDED.work_start,
                    work_end = EXCLUDED.work_end
            """, st["id"], st["day_of_week"], bool(st.get("is_day_off", 0)), ws, we)

        for br in sqlite_data.get("schedule_template_breaks", []):
            bs = parse_time(br["break_start"])
            be = parse_time(br["break_end"])
            await conn.execute("""
                INSERT INTO schedule_template_breaks (id, template_id, break_start, break_end)
                VALUES ($1, $2, $3, $4)
                ON CONFLICT (id) DO UPDATE SET break_start = EXCLUDED.break_start, break_end = EXCLUDED.break_end
            """, br["id"], br["template_id"], bs, be)

        # 8. Portfolio Categories
        for pc in sqlite_data.get("portfolio_categories", []):
            await conn.execute("""
                INSERT INTO portfolio_categories (id, master_id, title, display_order, is_active, created_at)
                VALUES ($1, 1, $2, $3, $4, $5)
                ON CONFLICT (id) DO UPDATE SET title = EXCLUDED.title, display_order = EXCLUDED.display_order
            """, pc["id"], pc["title"], pc.get("display_order", 0), bool(pc.get("is_active", 1)), parse_datetime(pc.get("created_at")))

        # 9. Appointments
        for app in sqlite_data.get("appointments", []):
            await conn.execute("""
                INSERT INTO appointments (
                    id, master_id, user_id, service_id, status, start_time, end_time, end_time_with_buffer,
                    hold_until, cancel_policy_agreed, cancel_reason, snapshot_service_title,
                    snapshot_service_price, snapshot_service_duration_min, snapshot_buffer_duration_min,
                    snapshot_deposit_amount, is_manual_by_admin, admin_notes, created_at, updated_at
                ) VALUES (
                    $1, 1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, $17, $18, $19
                )
                ON CONFLICT (id) DO UPDATE SET status = EXCLUDED.status, updated_at = EXCLUDED.updated_at
            """, app["id"], app["user_id"], app["service_id"], app["status"],
               parse_datetime(app["start_time"]), parse_datetime(app["end_time"]), parse_datetime(app["end_time_with_buffer"]),
               parse_datetime(app.get("hold_until")), bool(app.get("cancel_policy_agreed", 1)), app.get("cancel_reason"),
               app["snapshot_service_title"], Decimal(str(app["snapshot_service_price"])),
               app["snapshot_service_duration_min"], app["snapshot_buffer_duration_min"],
               Decimal(str(app["snapshot_deposit_amount"])), bool(app.get("is_manual_by_admin", 0)),
               app.get("admin_notes"), parse_datetime(app.get("created_at")), parse_datetime(app.get("updated_at")))

        # 10. Payments
        for p in sqlite_data.get("payments", []):
            await conn.execute("""
                INSERT INTO payments (
                    id, master_id, appointment_id, user_id, amount, status, created_at, confirmed_at, confirmed_by_admin_id, rejection_reason
                ) VALUES (
                    $1, 1, $2, $3, $4, $5, $6, $7, $8, $9
                )
                ON CONFLICT (id) DO UPDATE SET status = EXCLUDED.status, confirmed_at = EXCLUDED.confirmed_at
            """, p["id"], p["appointment_id"], p["user_id"], Decimal(str(p["amount"])),
               p["status"], parse_datetime(p.get("created_at")), parse_datetime(p.get("confirmed_at")),
               p.get("confirmed_by_admin_id"), p.get("rejection_reason"))

        # 11. Payment Proofs
        for pf in sqlite_data.get("payment_proofs", []):
            await conn.execute("""
                INSERT INTO payment_proofs (
                    id, payment_id, telegram_file_id, telegram_file_unique_id, media_type, user_comment, uploaded_at
                ) VALUES (
                    $1, $2, $3, $4, $5, $6, $7
                )
                ON CONFLICT (id) DO UPDATE SET telegram_file_id = EXCLUDED.telegram_file_id
            """, pf["id"], pf["payment_id"], pf["telegram_file_id"], pf["telegram_file_unique_id"],
               pf.get("media_type", "PHOTO"), pf.get("user_comment"), parse_datetime(pf.get("uploaded_at")))

        # 12. Reset sequences
        seq_tables = [
            ("users", "users_id_seq"),
            ("admins", "admins_id_seq"),
            ("masters", "masters_id_seq"),
            ("services", "services_id_seq"),
            ("schedule_templates", "schedule_templates_id_seq"),
            ("schedule_template_breaks", "schedule_template_breaks_id_seq"),
            ("portfolio_categories", "portfolio_categories_id_seq"),
            ("appointments", "appointments_id_seq"),
            ("payments", "payments_id_seq"),
            ("payment_proofs", "payment_proofs_id_seq"),
            ("master_clients", "master_clients_id_seq"),
            ("master_admins", "master_admins_id_seq"),
        ]
        for tbl, seq in seq_tables:
            try:
                await conn.execute(f"SELECT setval('{seq}', COALESCE((SELECT MAX(id) FROM {tbl}), 1))")
            except Exception:
                pass

    # Post-import counts & FK validation
    for t in sqlite_data.keys():
        try:
            c = await conn.fetchval(f'SELECT COUNT(*) FROM "{t}"')
            report["postgres_counts_after"][t] = c
        except Exception:
            report["postgres_counts_after"][t] = None

    # Validate FK relationships
    fk_queries = [
        ("appointments -> users", "SELECT COUNT(*) FROM appointments a LEFT JOIN users u ON a.user_id = u.id WHERE u.id IS NULL"),
        ("appointments -> services", "SELECT COUNT(*) FROM appointments a LEFT JOIN services s ON a.service_id = s.id WHERE s.id IS NULL"),
        ("appointments -> masters", "SELECT COUNT(*) FROM appointments a LEFT JOIN masters m ON a.master_id = m.id WHERE m.id IS NULL"),
        ("payments -> appointments", "SELECT COUNT(*) FROM payments p LEFT JOIN appointments a ON p.appointment_id = a.id WHERE a.id IS NULL"),
        ("payments -> masters (composite)", "SELECT COUNT(*) FROM payments p JOIN appointments a ON p.appointment_id = a.id WHERE p.master_id != a.master_id"),
        ("payment_proofs -> payments", "SELECT COUNT(*) FROM payment_proofs pf LEFT JOIN payments p ON pf.payment_id = p.id WHERE p.id IS NULL"),
        ("master_clients -> masters", "SELECT COUNT(*) FROM master_clients mc LEFT JOIN masters m ON mc.master_id = m.id WHERE m.id IS NULL"),
        ("master_clients -> users", "SELECT COUNT(*) FROM master_clients mc LEFT JOIN users u ON mc.user_id = u.id WHERE u.id IS NULL"),
    ]
    for check_name, q in fk_queries:
        orphans = await conn.fetchval(q)
        report["fk_checks"][check_name] = "PASSED (0 orphans)" if orphans == 0 else f"FAILED ({orphans} orphans)"

    await conn.close()
    return report


def main():
    parser = argparse.ArgumentParser(description="ETL script for migrating legacy SQLite to PostgreSQL.")
    parser.add_argument("--sqlite-path", default="data/beauty_bot.sqlite3.bak", help="Path to SQLite backup file")
    parser.add_argument(
        "--pg-url",
        default=os.environ.get("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/beauty_bot_db"),
        help="PostgreSQL connection URL",
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--dry-run", action="store_true", help="Analyze and validate without modifying target PostgreSQL")
    group.add_argument("--execute", action="store_true", help="Perform real idempotent data migration")

    args = parser.parse_args()
    report = asyncio.run(run_etl(args.sqlite_path, args.pg_url, execute=args.execute))

    print("\n" + "=" * 60)
    print(f"ETL MIGRATION REPORT ({report['mode']})")
    print("=" * 60)
    print(f"{'Table':<30} | {'SQLite':<8} | {'PG Before':<10} | {'PG After':<10}")
    print("-" * 65)
    for t, sq_c in report["sqlite_counts"].items():
        pg_b = report["postgres_counts_before"].get(t, "-")
        pg_a = report["postgres_counts_after"].get(t, "-")
        print(f"{t:<30} | {sq_c:<8} | {str(pg_b):<10} | {str(pg_a):<10}")

    if report["fk_checks"]:
        print("\n" + "-" * 60)
        print("FOREIGN KEY INTEGRITY CHECKS:")
        print("-" * 60)
        for check, res in report["fk_checks"].items():
            print(f"  - {check:<35}: {res}")
    print("=" * 60 + "\n")


if __name__ == "__main__":
    main()
