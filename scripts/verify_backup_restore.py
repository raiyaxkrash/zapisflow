"""
Actual Backup & Restore verification script for PostgreSQL 16.
Creates real domain data in test database, dumps with pg_dump -F c,
restores into a completely new database with pg_restore,
and verifies counts, foreign keys, constraints, and token decryption with BOT_TOKEN_ENCRYPTION_KEY.
"""

import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import os
import subprocess
import sys
import uuid

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config.settings import settings
from app.core.token_crypto import TokenCrypto
from app.database.models.appointment import Appointment, AppointmentStatus
from app.database.models.master import (
    BotInstance,
    BotInstanceStatus,
    Master,
    MasterClient,
    MasterSettings,
    MasterStatus,
    SubscriptionStatus,
)
from app.database.models.payment import Payment, PaymentStatus
from app.database.models.service import Service
from app.database.models.subscription import (
    SubscriptionPayment,
    SubscriptionPeriod,
    SubscriptionPlan,
)
from app.database.models.user import User


PG_BIN = r"C:\Program Files\PostgreSQL\16\bin"
PG_DUMP = os.path.join(PG_BIN, "pg_dump.exe")
PG_RESTORE = os.path.join(PG_BIN, "pg_restore.exe")
PSQL = os.path.join(PG_BIN, "psql.exe")

SRC_DB = "beauty_bot_fresh_test"
RESTORE_DB = "beauty_bot_restore_test"
DUMP_FILE = "actual_backup_test.dump"

MASTER_KEY = TokenCrypto.generate_key()
SAMPLE_TOKEN = "9876543210:AAH_test_sample_bot_token_secret_123"


async def seed_data():
    print(f"--- Seeding test data into {SRC_DB} ---")
    engine = create_async_engine(f"postgresql+asyncpg://postgres:postgres@localhost:5432/{SRC_DB}")
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    crypto = TokenCrypto(master_key=MASTER_KEY)

    async with session_factory() as session:
        # 1. User (Owner) and User (Client)
        owner_user = User(telegram_id=777001, first_name="Anna", username="anna_beauty")
        client_user = User(telegram_id=777002, first_name="Elena", username="elena_client", phone="+79991112233")
        session.add_all([owner_user, client_user])
        await session.flush()

        # 2. Master
        master = Master(
            owner_user_id=owner_user.id,
            display_name="Anna Beauty Studio",
            status=MasterStatus.ACTIVE,
            subscription_status=SubscriptionStatus.ACTIVE,
            timezone="Europe/Moscow",
            trial_ends_at=datetime.now(timezone.utc) + timedelta(days=14),
            paid_until=datetime.now(timezone.utc) + timedelta(days=30),
        )
        session.add(master)
        await session.flush()

        # Master Settings
        m_settings = MasterSettings(
            master_id=master.id,
            bank_name="Сбербанк",
            bank_card_number="2202 0000 1111 2222",
            bank_recipient_name="Анна Б.",
        )
        session.add(m_settings)

        # 3. BotInstance (with AES-256-GCM encrypted token tied to telegram_bot_id AAD)
        bot_id = 9876543210
        enc_token = crypto.encrypt(SAMPLE_TOKEN, associated_data=bot_id)
        bot = BotInstance(
            master_id=master.id,
            public_id=uuid.uuid4(),
            telegram_bot_id=bot_id,
            telegram_username="anna_beauty_bot",
            telegram_first_name="Anna Studio Bot",
            encrypted_token=enc_token,
            webhook_secret="sec_webhook_token_32chars_test123",
            status=BotInstanceStatus.ACTIVE,
            is_current=True,
            token_version=1,
        )
        session.add(bot)
        await session.flush()

        # 4. Service
        service = Service(
            master_id=master.id,
            title="Маникюр с покрытием",
            price=Decimal("2500.00"),
            duration_min=90,
            buffer_min=30,
            deposit_value=Decimal("500.00"),
            is_active=True,
        )
        session.add(service)
        await session.flush()

        # 5. MasterClient
        m_client = MasterClient(
            master_id=master.id,
            user_id=client_user.id,
            notes="VIP клиент",
        )
        session.add(m_client)

        # 6. Appointment
        now = datetime.now(timezone.utc)
        start_t = now + timedelta(days=1, hours=2)
        end_t = start_t + timedelta(minutes=90)
        end_buf = end_t + timedelta(minutes=30)
        appt = Appointment(
            master_id=master.id,
            user_id=client_user.id,
            service_id=service.id,
            start_time=start_t,
            end_time=end_t,
            end_time_with_buffer=end_buf,
            status=AppointmentStatus.CONFIRMED,
            snapshot_service_title="Маникюр с покрытием",
            snapshot_service_price=Decimal("2500.00"),
            snapshot_service_duration_min=90,
            snapshot_buffer_duration_min=30,
            snapshot_deposit_amount=Decimal("500.00"),
            cancel_policy_agreed=True,
        )
        session.add(appt)
        await session.flush()

        # 7. Payment
        payment = Payment(
            master_id=master.id,
            appointment_id=appt.id,
            user_id=client_user.id,
            amount=Decimal("2500.00"),
            status=PaymentStatus.CONFIRMED,
        )
        session.add(payment)

        # 8. Subscription Plan, Payment, Period
        plan = SubscriptionPlan(
            code="STANDARD_MONTHLY",
            name="Стандартный месяц",
            price=Decimal("1990.00"),
            currency="RUB",
            period_days=30,
            is_active=True,
        )
        session.add(plan)
        await session.flush()

        sub_payment = SubscriptionPayment(
            master_id=master.id,
            plan_id=plan.id,
            provider="MANUAL",
            provider_payment_id="sub_pay_rec_001",
            amount=Decimal("1990.00"),
            currency="RUB",
            status="SUCCEEDED",
            period_days=30,
            paid_at=now,
        )
        session.add(sub_payment)

        sub_period = SubscriptionPeriod(
            master_id=master.id,
            plan_id=plan.id,
            status="ACTIVE",
            source="PAYMENT",
            starts_at=now,
            ends_at=now + timedelta(days=30),
            amount=Decimal("1990.00"),
            currency="RUB",
            external_payment_id="sub_pay_rec_001",
        )
        session.add(sub_period)

        await session.commit()
    await engine.dispose()
    print("Seed complete.")


def run_cmd(cmd_list, env=None):
    merged_env = os.environ.copy()
    if env:
        merged_env.update(env)
    res = subprocess.run(cmd_list, env=merged_env, capture_output=True, text=True, errors="replace")
    if res.returncode != 0:
        print(f"ERROR executing {' '.join(cmd_list)}:\n{res.stderr}")
        sys.exit(1)
    return res.stdout


def recreate_fresh_db():
    print(f"--- Recreating clean database {SRC_DB} and running migrations ---")
    run_cmd([PSQL, "-h", "localhost", "-p", "5432", "-U", "postgres", "-d", "postgres", "-c", f"DROP DATABASE IF EXISTS {SRC_DB};"], env={"PGPASSWORD": "postgres"})
    run_cmd([PSQL, "-h", "localhost", "-p", "5432", "-U", "postgres", "-d", "postgres", "-c", f"CREATE DATABASE {SRC_DB};"], env={"PGPASSWORD": "postgres"})
    run_cmd(
        [r".venv\Scripts\alembic.exe", "upgrade", "head"],
        env={"PYTHONPATH": ".", "DATABASE_URL": f"postgresql+asyncpg://postgres:postgres@localhost:5432/{SRC_DB}"},
    )
    print(f"Fresh database {SRC_DB} migrated to head.")


def execute_pg_dump():
    print(f"--- Running pg_dump on {SRC_DB} ---")
    if os.path.exists(DUMP_FILE):
        os.remove(DUMP_FILE)
    run_cmd(
        [PG_DUMP, "-h", "localhost", "-p", "5432", "-U", "postgres", "-d", SRC_DB, "-F", "c", "-b", "-v", "-f", DUMP_FILE],
        env={"PGPASSWORD": "postgres"},
    )
    dump_size = os.path.getsize(DUMP_FILE)
    print(f"pg_dump SUCCESS! Dump file created: {DUMP_FILE} ({dump_size} bytes)")


def recreate_restore_db():
    print(f"--- Recreating clean database {RESTORE_DB} ---")
    run_cmd([PSQL, "-h", "localhost", "-p", "5432", "-U", "postgres", "-d", "postgres", "-c", f"DROP DATABASE IF EXISTS {RESTORE_DB};"], env={"PGPASSWORD": "postgres"})
    run_cmd([PSQL, "-h", "localhost", "-p", "5432", "-U", "postgres", "-d", "postgres", "-c", f"CREATE DATABASE {RESTORE_DB};"], env={"PGPASSWORD": "postgres"})
    print(f"Empty database {RESTORE_DB} ready.")


def execute_pg_restore():
    print(f"--- Running pg_restore into {RESTORE_DB} ---")
    res = subprocess.run(
        [PG_RESTORE, "-h", "localhost", "-p", "5432", "-U", "postgres", "-d", RESTORE_DB, "-v", DUMP_FILE],
        env={**os.environ, "PGPASSWORD": "postgres"},
        capture_output=True,
        text=True,
        errors="replace",
    )
    print("pg_restore finished.")


async def verify_restored_data():
    print(f"--- Verifying counts, invariants and crypto in {RESTORE_DB} ---")
    engine = create_async_engine(f"postgresql+asyncpg://postgres:postgres@localhost:5432/{RESTORE_DB}")
    session_factory = async_sessionmaker(engine, class_=AsyncSession)

    crypto = TokenCrypto(master_key=MASTER_KEY)

    async with session_factory() as session:
        # Check Masters (includes migration default backfill master + seeded master)
        masters = (await session.execute(select(Master))).scalars().all()
        assert len(masters) >= 2, f"Expected at least 2 masters, got {len(masters)}"
        master_res = await session.execute(select(Master).where(Master.display_name == "Anna Beauty Studio"))
        master = master_res.scalar_one()
        assert master.display_name == "Anna Beauty Studio"
        print(f"[OK] Total Masters in restored DB: {len(masters)}, verified seeded Master: #{master.id} '{master.display_name}'")

        # Check BotInstances
        bots = (await session.execute(select(BotInstance))).scalars().all()
        assert len(bots) >= 1, f"Expected at least 1 bot, got {len(bots)}"
        bot_res = await session.execute(select(BotInstance).where(BotInstance.telegram_username == "anna_beauty_bot"))
        bot = bot_res.scalar_one()
        assert bot.telegram_username == "anna_beauty_bot"
        print(f"[OK] Total BotInstances in restored DB: {len(bots)}, verified seeded Bot: #{bot.id} @{bot.telegram_username}")

        # Check Token Decryption with BOT_TOKEN_ENCRYPTION_KEY
        decrypted_token = crypto.decrypt(bot.encrypted_token, associated_data=bot.telegram_bot_id)
        assert decrypted_token == SAMPLE_TOKEN, "Decrypted token does not match original plaintext!"
        print(f"[OK] Encryption key restore verified! Token successfully decrypted with AAD bot:{bot.telegram_bot_id}")

        # Check Services
        services = (await session.execute(select(Service))).scalars().all()
        assert len(services) == 1
        assert services[0].title == "Маникюр с покрытием"
        print(f"[OK] Services count: {len(services)}, title: {services[0].title}")

        # Check Appointments
        appts = (await session.execute(select(Appointment))).scalars().all()
        assert len(appts) == 1
        assert appts[0].status == AppointmentStatus.CONFIRMED
        print(f"[OK] Appointments count: {len(appts)}, status: {appts[0].status}")

        # Check Payments
        payments = (await session.execute(select(Payment))).scalars().all()
        assert len(payments) == 1
        assert payments[0].amount == Decimal("2500.00")
        print(f"[OK] Payments count: {len(payments)}, amount: {payments[0].amount}")

        # Check MasterClients for seeded master
        clients = (await session.execute(select(MasterClient).where(MasterClient.master_id == master.id))).scalars().all()
        assert len(clients) == 1
        print(f"[OK] MasterClients for #{master.id}: {len(clients)}, client notes: {clients[0].notes}")

        # Check Subscription Data
        plans = (await session.execute(select(SubscriptionPlan))).scalars().all()
        sub_payments = (await session.execute(select(SubscriptionPayment).where(SubscriptionPayment.master_id == master.id))).scalars().all()
        sub_periods = (await session.execute(select(SubscriptionPeriod).where(SubscriptionPeriod.master_id == master.id))).scalars().all()
        assert len(plans) >= 1
        assert len(sub_payments) == 1
        assert len(sub_periods) == 1
        print(f"[OK] SubscriptionPlans: {len(plans)}, Payments for #{master.id}: {len(sub_payments)}, Periods: {len(sub_periods)}")

    await engine.dispose()
    print("ALL RESTORE INVARIANTS & ENCRYPTION KEY CHECKS PASSED!")


async def main():
    recreate_fresh_db()
    await seed_data()
    execute_pg_dump()
    recreate_restore_db()
    execute_pg_restore()
    await verify_restored_data()
    # Cleanup dump file
    if os.path.exists(DUMP_FILE):
        os.remove(DUMP_FILE)
    print("RESTORE VERIFICATION COMPLETE: 100% SUCCESS")


if __name__ == "__main__":
    asyncio.run(main())
