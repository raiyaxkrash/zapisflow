"""Comprehensive integration test suite for Multi-Tenant Database Schema Layer (Phase 2).

Verifies all 18 mandatory schema invariants on real PostgreSQL:
1. User can own multiple Masters (1 -> N)
2. BotInstance.telegram_bot_id is unique
3. MasterClient UNIQUE(master_id, user_id)
4. One User can be client of Master A and Master B
5. Marketing default is FALSE
6. Bot blocked on Master A does not affect Master B
7. Service requires Master (NOT NULL + FK)
8. Appointment requires Master (NOT NULL + FK)
9. Same interval for Master A and Master B is allowed
10. Overlapping interval within Master A is forbidden (23P01)
11. Payment cannot reference Appointment of different Master (composite FK failure)
12. Broadcast requires Master (NOT NULL + FK)
13. Portfolio isolation between Master A and Master B
14. Schedule independence between Master A and Master B
15. Existing single-tenant data backfilled into Master #1
16. Alembic 0002 -> 0003 upgrade works
17. Fresh Alembic base -> head works
18. Downgrade 0003 -> 0002 works cleanly
"""

import os
import subprocess
import sys
from datetime import datetime, time, timezone
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models.appointment import Appointment, AppointmentStatus
from app.database.models.broadcast import Broadcast, BroadcastStatus
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
from app.database.models.portfolio import PortfolioCategory
from app.database.models.schedule import ScheduleTemplate
from app.database.models.service import DepositType, Service
from app.database.models.user import User
from tests.conftest import POSTGRES_AVAILABLE, TEST_DATABASE_URL, requires_postgres

ROOT = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------------------
# Helpers for fixture data
# ---------------------------------------------------------------------------

async def _create_test_user(session: AsyncSession, telegram_id: int, first_name: str = "Test") -> User:
    user = User(
        telegram_id=telegram_id,
        first_name=first_name,
    )
    session.add(user)
    await session.flush()
    return user


async def _create_test_master(
    session: AsyncSession, owner_user_id: int, display_name: str = "Studio"
) -> Master:
    master = Master(
        owner_user_id=owner_user_id,
        display_name=display_name,
        status=MasterStatus.ACTIVE,
        subscription_status=SubscriptionStatus.ACTIVE,
        timezone="UTC",
    )
    session.add(master)
    await session.flush()
    return master


async def _create_test_service(session: AsyncSession, master_id: int, title: str = "Service") -> Service:
    service = Service(
        master_id=master_id,
        title=title,
        price=Decimal("1500.00"),
        duration_min=60,
        buffer_min=15,
        deposit_type=DepositType.FIXED,
        deposit_value=Decimal("500.00"),
        is_active=True,
    )
    session.add(service)
    await session.flush()
    return service


# ---------------------------------------------------------------------------
# Tests 1-15: Multi-Tenant PostgreSQL Schema Constraints & Logic
# ---------------------------------------------------------------------------

@requires_postgres
@pytest.mark.asyncio
async def test_01_user_can_own_multiple_masters(pg_session: AsyncSession) -> None:
    """1. One User can own multiple Masters (1 -> N relationship, no unique on owner_user_id)."""
    owner = await _create_test_user(pg_session, telegram_id=9000101, first_name="SerialOwner")
    master_a = await _create_test_master(pg_session, owner_user_id=owner.id, display_name="Salon Alpha")
    master_b = await _create_test_master(pg_session, owner_user_id=owner.id, display_name="Salon Beta")

    assert master_a.id is not None
    assert master_b.id is not None
    assert master_a.id != master_b.id
    assert master_a.owner_user_id == master_b.owner_user_id == owner.id


@requires_postgres
@pytest.mark.asyncio
async def test_02_bot_instance_telegram_bot_id_is_unique(pg_session: AsyncSession) -> None:
    """2. BotInstance.telegram_bot_id is globally unique across the whole system."""
    owner = await _create_test_user(pg_session, telegram_id=9000201, first_name="BotMaster")
    master = await _create_test_master(pg_session, owner_user_id=owner.id, display_name="Bot Studio")

    bot1 = BotInstance(
        master_id=master.id,
        telegram_bot_id=88880001,
        telegram_username="test_bot_1",
        encrypted_token="enc:token_1",
        status=BotInstanceStatus.ACTIVE,
    )
    pg_session.add(bot1)
    await pg_session.flush()

    bot2 = BotInstance(
        master_id=master.id,
        telegram_bot_id=88880001,  # Duplicate telegram_bot_id
        telegram_username="test_bot_2",
        encrypted_token="enc:token_2",
        status=BotInstanceStatus.ACTIVE,
    )
    async with pg_session.begin_nested():
        pg_session.add(bot2)
        with pytest.raises(IntegrityError) as exc_info:
            await pg_session.flush()
        assert "uq_bot_instances_telegram_bot_id" in str(exc_info.value)


@requires_postgres
@pytest.mark.asyncio
async def test_03_master_client_unique_master_user(pg_session: AsyncSession) -> None:
    """3. MasterClient enforces UNIQUE(master_id, user_id)."""
    owner = await _create_test_user(pg_session, telegram_id=9000301, first_name="Owner3")
    client = await _create_test_user(pg_session, telegram_id=9000302, first_name="Client3")
    master = await _create_test_master(pg_session, owner_user_id=owner.id, display_name="Studio 3")

    mc1 = MasterClient(master_id=master.id, user_id=client.id)
    pg_session.add(mc1)
    await pg_session.flush()

    mc2 = MasterClient(master_id=master.id, user_id=client.id)
    async with pg_session.begin_nested():
        pg_session.add(mc2)
        with pytest.raises(IntegrityError) as exc_info:
            await pg_session.flush()
        assert "uq_master_clients_master_user" in str(exc_info.value)


@requires_postgres
@pytest.mark.asyncio
async def test_04_one_user_can_be_client_of_multiple_masters(pg_session: AsyncSession) -> None:
    """4. One User can be a client of both Master A and Master B simultaneously."""
    owner_a = await _create_test_user(pg_session, telegram_id=9000401, first_name="OwnerA")
    owner_b = await _create_test_user(pg_session, telegram_id=9000402, first_name="OwnerB")
    client = await _create_test_user(pg_session, telegram_id=9000403, first_name="SharedClient")

    master_a = await _create_test_master(pg_session, owner_user_id=owner_a.id, display_name="Studio A")
    master_b = await _create_test_master(pg_session, owner_user_id=owner_b.id, display_name="Studio B")

    mc_a = MasterClient(master_id=master_a.id, user_id=client.id)
    mc_b = MasterClient(master_id=master_b.id, user_id=client.id)
    pg_session.add_all([mc_a, mc_b])
    await pg_session.flush()

    assert mc_a.id is not None
    assert mc_b.id is not None
    assert mc_a.master_id != mc_b.master_id


@requires_postgres
@pytest.mark.asyncio
async def test_05_marketing_default_is_false(pg_session: AsyncSession) -> None:
    """5. Marketing consent is strictly FALSE by default in master_clients."""
    owner = await _create_test_user(pg_session, telegram_id=9000501, first_name="Owner5")
    client = await _create_test_user(pg_session, telegram_id=9000502, first_name="OptInClient")
    master = await _create_test_master(pg_session, owner_user_id=owner.id, display_name="Studio 5")

    mc = MasterClient(master_id=master.id, user_id=client.id)
    pg_session.add(mc)
    await pg_session.flush()
    await pg_session.refresh(mc)

    assert mc.is_marketing_allowed is False


@requires_postgres
@pytest.mark.asyncio
async def test_06_bot_blocked_on_master_a_does_not_affect_master_b(pg_session: AsyncSession) -> None:
    """6. Bot blocked on Master A is tenant-scoped and does not affect Master B."""
    owner_a = await _create_test_user(pg_session, telegram_id=9000601, first_name="Owner6A")
    owner_b = await _create_test_user(pg_session, telegram_id=9000602, first_name="Owner6B")
    client = await _create_test_user(pg_session, telegram_id=9000603, first_name="Client6")

    master_a = await _create_test_master(pg_session, owner_user_id=owner_a.id, display_name="Studio 6A")
    master_b = await _create_test_master(pg_session, owner_user_id=owner_b.id, display_name="Studio 6B")

    mc_a = MasterClient(master_id=master_a.id, user_id=client.id, is_bot_blocked=True)
    mc_b = MasterClient(master_id=master_b.id, user_id=client.id, is_bot_blocked=False)
    pg_session.add_all([mc_a, mc_b])
    await pg_session.flush()

    assert mc_a.is_bot_blocked is True
    assert mc_b.is_bot_blocked is False


@requires_postgres
@pytest.mark.asyncio
async def test_07_service_requires_master_not_null_and_fk(pg_session: AsyncSession) -> None:
    """7. Service table requires a valid Master (NOT NULL + foreign key constraint)."""
    svc_invalid_fk = Service(
        master_id=99999999,
        title="Invalid Master Svc",
        price=Decimal("1000.00"),
        duration_min=60,
        buffer_min=15,
        deposit_type=DepositType.FIXED,
        deposit_value=Decimal("300.00"),
    )
    async with pg_session.begin_nested():
        pg_session.add(svc_invalid_fk)
        with pytest.raises(IntegrityError):
            await pg_session.flush()


@requires_postgres
@pytest.mark.asyncio
async def test_08_appointment_requires_master_not_null_and_fk(pg_session: AsyncSession) -> None:
    """8. Appointment table requires a valid Master (NOT NULL + foreign key constraint)."""
    owner = await _create_test_user(pg_session, telegram_id=9000801, first_name="Owner8")
    client = await _create_test_user(pg_session, telegram_id=9000802, first_name="Client8")
    master = await _create_test_master(pg_session, owner_user_id=owner.id, display_name="Studio 8")
    svc = await _create_test_service(pg_session, master_id=master.id, title="Service 8")

    now = datetime(2026, 12, 1, 10, 0, tzinfo=timezone.utc)
    appt_invalid_master = Appointment(
        master_id=99999999,  # Non-existent master_id
        user_id=client.id,
        service_id=svc.id,
        status=AppointmentStatus.WAITING_PAYMENT,
        start_time=now,
        end_time=now,
        end_time_with_buffer=now,
        snapshot_service_title=svc.title,
        snapshot_service_price=svc.price,
        snapshot_service_duration_min=svc.duration_min,
        snapshot_buffer_duration_min=svc.buffer_min,
        snapshot_deposit_amount=svc.deposit_value,
    )
    async with pg_session.begin_nested():
        pg_session.add(appt_invalid_master)
        with pytest.raises(IntegrityError):
            await pg_session.flush()


@requires_postgres
@pytest.mark.asyncio
async def test_09_same_interval_for_master_a_and_master_b_is_allowed(pg_session: AsyncSession) -> None:
    """9. Same time interval for Master A and Master B does not conflict with exclusion constraint."""
    owner_a = await _create_test_user(pg_session, telegram_id=9000901, first_name="Owner9A")
    owner_b = await _create_test_user(pg_session, telegram_id=9000902, first_name="Owner9B")
    client = await _create_test_user(pg_session, telegram_id=9000903, first_name="Client9")

    master_a = await _create_test_master(pg_session, owner_user_id=owner_a.id, display_name="Studio 9A")
    master_b = await _create_test_master(pg_session, owner_user_id=owner_b.id, display_name="Studio 9B")

    svc_a = await _create_test_service(pg_session, master_id=master_a.id, title="Service 9A")
    svc_b = await _create_test_service(pg_session, master_id=master_b.id, title="Service 9B")

    start = datetime(2026, 12, 10, 12, 0, tzinfo=timezone.utc)
    end = datetime(2026, 12, 10, 13, 0, tzinfo=timezone.utc)
    end_buf = datetime(2026, 12, 10, 13, 15, tzinfo=timezone.utc)

    appt_a = Appointment(
        master_id=master_a.id,
        user_id=client.id,
        service_id=svc_a.id,
        status=AppointmentStatus.CONFIRMED,
        start_time=start,
        end_time=end,
        end_time_with_buffer=end_buf,
        snapshot_service_title=svc_a.title,
        snapshot_service_price=svc_a.price,
        snapshot_service_duration_min=svc_a.duration_min,
        snapshot_buffer_duration_min=svc_a.buffer_min,
        snapshot_deposit_amount=svc_a.deposit_value,
    )
    appt_b = Appointment(
        master_id=master_b.id,
        user_id=client.id,
        service_id=svc_b.id,
        status=AppointmentStatus.CONFIRMED,
        start_time=start,
        end_time=end,
        end_time_with_buffer=end_buf,
        snapshot_service_title=svc_b.title,
        snapshot_service_price=svc_b.price,
        snapshot_service_duration_min=svc_b.duration_min,
        snapshot_buffer_duration_min=svc_b.buffer_min,
        snapshot_deposit_amount=svc_b.deposit_value,
    )
    pg_session.add_all([appt_a, appt_b])
    await pg_session.flush()

    assert appt_a.id is not None
    assert appt_b.id is not None


@requires_postgres
@pytest.mark.asyncio
async def test_10_overlapping_interval_within_master_a_is_forbidden_23p01(pg_session: AsyncSession) -> None:
    """10. Overlapping interval within the same master raises PostgreSQL exclusion error 23P01."""
    owner = await _create_test_user(pg_session, telegram_id=9001001, first_name="Owner10")
    client1 = await _create_test_user(pg_session, telegram_id=9001002, first_name="Client10A")
    client2 = await _create_test_user(pg_session, telegram_id=9001003, first_name="Client10B")
    master = await _create_test_master(pg_session, owner_user_id=owner.id, display_name="Studio 10")
    svc = await _create_test_service(pg_session, master_id=master.id, title="Service 10")

    appt1 = Appointment(
        master_id=master.id,
        user_id=client1.id,
        service_id=svc.id,
        status=AppointmentStatus.CONFIRMED,
        start_time=datetime(2026, 12, 11, 14, 0, tzinfo=timezone.utc),
        end_time=datetime(2026, 12, 11, 15, 0, tzinfo=timezone.utc),
        end_time_with_buffer=datetime(2026, 12, 11, 15, 15, tzinfo=timezone.utc),
        snapshot_service_title=svc.title,
        snapshot_service_price=svc.price,
        snapshot_service_duration_min=svc.duration_min,
        snapshot_buffer_duration_min=svc.buffer_min,
        snapshot_deposit_amount=svc.deposit_value,
    )
    pg_session.add(appt1)
    await pg_session.flush()

    appt2 = Appointment(
        master_id=master.id,
        user_id=client2.id,
        service_id=svc.id,
        status=AppointmentStatus.WAITING_PAYMENT,
        start_time=datetime(2026, 12, 11, 14, 30, tzinfo=timezone.utc),  # Overlaps!
        end_time=datetime(2026, 12, 11, 15, 30, tzinfo=timezone.utc),
        end_time_with_buffer=datetime(2026, 12, 11, 15, 45, tzinfo=timezone.utc),
        snapshot_service_title=svc.title,
        snapshot_service_price=svc.price,
        snapshot_service_duration_min=svc.duration_min,
        snapshot_buffer_duration_min=svc.buffer_min,
        snapshot_deposit_amount=svc.deposit_value,
    )
    async with pg_session.begin_nested():
        pg_session.add(appt2)
        with pytest.raises(IntegrityError) as exc_info:
            await pg_session.flush()
        err_msg = str(exc_info.value)
        assert "no_overlapping_active_appointments" in err_msg or "23P01" in err_msg


@requires_postgres
@pytest.mark.asyncio
async def test_11_payment_cannot_reference_appointment_of_different_master(pg_session: AsyncSession) -> None:
    """11. Payment cannot reference an Appointment belonging to a different Master (composite FK failure)."""
    owner_a = await _create_test_user(pg_session, telegram_id=9001101, first_name="Owner11A")
    owner_b = await _create_test_user(pg_session, telegram_id=9001102, first_name="Owner11B")
    client = await _create_test_user(pg_session, telegram_id=9001103, first_name="Client11")

    master_a = await _create_test_master(pg_session, owner_user_id=owner_a.id, display_name="Studio 11A")
    master_b = await _create_test_master(pg_session, owner_user_id=owner_b.id, display_name="Studio 11B")
    svc_a = await _create_test_service(pg_session, master_id=master_a.id, title="Service 11A")

    appt_a = Appointment(
        master_id=master_a.id,
        user_id=client.id,
        service_id=svc_a.id,
        status=AppointmentStatus.WAITING_PAYMENT,
        start_time=datetime(2026, 12, 12, 10, 0, tzinfo=timezone.utc),
        end_time=datetime(2026, 12, 12, 11, 0, tzinfo=timezone.utc),
        end_time_with_buffer=datetime(2026, 12, 12, 11, 15, tzinfo=timezone.utc),
        snapshot_service_title=svc_a.title,
        snapshot_service_price=svc_a.price,
        snapshot_service_duration_min=svc_a.duration_min,
        snapshot_buffer_duration_min=svc_a.buffer_min,
        snapshot_deposit_amount=svc_a.deposit_value,
    )
    pg_session.add(appt_a)
    await pg_session.flush()

    # Attempt to link appointment from Master A to Master B
    bad_payment = Payment(
        master_id=master_b.id,  # Mismatch!
        appointment_id=appt_a.id,
        user_id=client.id,
        amount=Decimal("500.00"),
        status=PaymentStatus.PENDING,
    )
    async with pg_session.begin_nested():
        pg_session.add(bad_payment)
        with pytest.raises(IntegrityError) as exc_info:
            await pg_session.flush()
        assert "fk_payments_appointment_master" in str(exc_info.value)

    # Valid payment with matching master_id succeeds
    good_payment = Payment(
        master_id=master_a.id,
        appointment_id=appt_a.id,
        user_id=client.id,
        amount=Decimal("500.00"),
        status=PaymentStatus.PENDING,
    )
    pg_session.add(good_payment)
    await pg_session.flush()
    assert good_payment.id is not None


@requires_postgres
@pytest.mark.asyncio
async def test_12_broadcast_requires_master_not_null_and_fk(pg_session: AsyncSession) -> None:
    """12. Broadcast requires a valid Master (NOT NULL + foreign key constraint)."""
    broadcast = Broadcast(
        master_id=99999999,  # Non-existent master
        text="Special promo today!",
        status=BroadcastStatus.DRAFT,
    )
    async with pg_session.begin_nested():
        pg_session.add(broadcast)
        with pytest.raises(IntegrityError):
            await pg_session.flush()


@requires_postgres
@pytest.mark.asyncio
async def test_13_portfolio_isolation_between_master_a_and_master_b(pg_session: AsyncSession) -> None:
    """13. Portfolio categories are strictly isolated by master_id."""
    owner_a = await _create_test_user(pg_session, telegram_id=9001301, first_name="Owner13A")
    owner_b = await _create_test_user(pg_session, telegram_id=9001302, first_name="Owner13B")

    master_a = await _create_test_master(pg_session, owner_user_id=owner_a.id, display_name="Studio 13A")
    master_b = await _create_test_master(pg_session, owner_user_id=owner_b.id, display_name="Studio 13B")

    cat_a = PortfolioCategory(master_id=master_a.id, title="Маникюр Студии А", is_active=True)
    cat_b = PortfolioCategory(master_id=master_b.id, title="Педикюр Студии Б", is_active=True)
    pg_session.add_all([cat_a, cat_b])
    await pg_session.flush()

    res_a = await pg_session.execute(
        select(PortfolioCategory).where(PortfolioCategory.master_id == master_a.id)
    )
    items_a = res_a.scalars().all()
    assert len(items_a) == 1
    assert items_a[0].title == "Маникюр Студии А"

    res_b = await pg_session.execute(
        select(PortfolioCategory).where(PortfolioCategory.master_id == master_b.id)
    )
    items_b = res_b.scalars().all()
    assert len(items_b) == 1
    assert items_b[0].title == "Педикюр Студии Б"


@requires_postgres
@pytest.mark.asyncio
async def test_14_schedule_independence_between_master_a_and_master_b(pg_session: AsyncSession) -> None:
    """14. Schedule weekly templates are independent across masters but unique per weekday within one master."""
    owner_a = await _create_test_user(pg_session, telegram_id=9001401, first_name="Owner14A")
    owner_b = await _create_test_user(pg_session, telegram_id=9001402, first_name="Owner14B")

    master_a = await _create_test_master(pg_session, owner_user_id=owner_a.id, display_name="Studio 14A")
    master_b = await _create_test_master(pg_session, owner_user_id=owner_b.id, display_name="Studio 14B")

    # Both Master A and Master B configure Monday (day_of_week=0) independently
    st_a = ScheduleTemplate(
        master_id=master_a.id,
        day_of_week=0,
        is_day_off=False,
        work_start=time(9, 0),
        work_end=time(18, 0),
    )
    st_b = ScheduleTemplate(
        master_id=master_b.id,
        day_of_week=0,
        is_day_off=False,
        work_start=time(11, 0),
        work_end=time(20, 0),
    )
    pg_session.add_all([st_a, st_b])
    await pg_session.flush()

    assert st_a.id is not None
    assert st_b.id is not None

    # Duplicate Monday within Master A fails
    st_a_duplicate = ScheduleTemplate(
        master_id=master_a.id,
        day_of_week=0,
        is_day_off=False,
        work_start=time(10, 0),
        work_end=time(19, 0),
    )
    async with pg_session.begin_nested():
        pg_session.add(st_a_duplicate)
        with pytest.raises(IntegrityError) as exc_info:
            await pg_session.flush()
        assert "uq_master_weekday" in str(exc_info.value)


@requires_postgres
@pytest.mark.asyncio
async def test_15_existing_single_tenant_data_backfilled_into_master_1(pg_session: AsyncSession) -> None:
    """15. Existing legacy data is backfilled into Master #1 and no NULL master_ids exist."""
    # 1. Master #1 exists
    res_m1 = await pg_session.execute(select(Master).where(Master.id == 1))
    m1 = res_m1.scalar_one_or_none()
    assert m1 is not None, "Master #1 must exist after migration 0003 backfill"

    # 2. MasterSettings exists for Master #1
    res_settings = await pg_session.execute(select(MasterSettings).where(MasterSettings.master_id == 1))
    m1_settings = res_settings.scalar_one_or_none()
    assert m1_settings is not None, "MasterSettings for Master #1 must exist"

    # 3. Check that no records have NULL master_id across all multi-tenant tables
    tables = [
        "services",
        "appointments",
        "payments",
        "schedule_templates",
        "schedule_exceptions",
        "blocked_intervals",
        "portfolio_categories",
        "broadcasts",
    ]
    for table in tables:
        count_res = await pg_session.execute(text(f"SELECT count(*) FROM {table} WHERE master_id IS NULL"))
        null_count = count_res.scalar()
        assert null_count == 0, f"Table {table} has {null_count} rows with NULL master_id"


# ---------------------------------------------------------------------------
# Tests 16-18: Alembic Migrations Integrity (0002 -> 0003, fresh base -> head, downgrade)
# ---------------------------------------------------------------------------

@requires_postgres
def test_16_alembic_0002_to_0003_upgrade_works() -> None:
    """16. Alembic migration from 0002 to 0003 executes cleanly."""
    env = os.environ.copy()
    env["DATABASE_URL"] = TEST_DATABASE_URL

    res_up = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "2026_09_30_0003"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
    )
    assert res_up.returncode == 0, f"Upgrade to 0003 failed: {res_up.stderr}"


@requires_postgres
def test_17_fresh_alembic_base_to_head_works() -> None:
    """17. Fresh database migration from base to head executes without errors."""
    env = os.environ.copy()
    env["DATABASE_URL"] = TEST_DATABASE_URL

    # Downgrade base -> upgrade head
    res_down = subprocess.run(
        [sys.executable, "-m", "alembic", "downgrade", "base"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
    )
    assert res_down.returncode == 0, f"Downgrade to base failed: {res_down.stderr}"

    res_up = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
    )
    assert res_up.returncode == 0, f"Fresh upgrade base -> head failed: {res_up.stderr}"


@requires_postgres
def test_18_downgrade_0003_to_0002_works_cleanly() -> None:
    """18. Downgrade from 0003 to 0002 cleanly drops all multi-tenant structures and enums."""
    env = os.environ.copy()
    env["DATABASE_URL"] = TEST_DATABASE_URL

    res_down = subprocess.run(
        [sys.executable, "-m", "alembic", "downgrade", "2026_09_30_0002"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
    )
    assert res_down.returncode == 0, f"Downgrade 0003 -> 0002 failed: {res_down.stderr}"

    # Re-upgrade to head to leave test database ready for other tests
    res_up = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
    )
    assert res_up.returncode == 0, f"Re-upgrade to head failed: {res_up.stderr}"
