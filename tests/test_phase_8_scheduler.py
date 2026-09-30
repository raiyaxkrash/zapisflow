"""Comprehensive integration tests for Phase 8: Multi-Replica Multi-Tenant Scheduler & Reliable Background Jobs.

Verifies:
1. Hold cleaner concurrency: atomic SELECT ... FOR UPDATE SKIP LOCKED prevents duplicate expirations across replicas.
2. Protection of PAYMENT_PROOF_SENT from expiration.
3. Multi-tenant bot routing in hold cleaner.
4. Durability: DB expiration committed before external Telegram calls.
5. Idempotent visit reminder generation via ON CONFLICT DO NOTHING.
6. MasterSettings toggles (24h/3h) and appointment cancellation handling.
7. Reminder delivery worker concurrent claims (FOR UPDATE SKIP LOCKED, zero duplicates).
8. TelegramForbiddenError handling (marks MasterClient.is_bot_blocked = True for that master only).
9. Rate limit handling (TelegramRetryAfter) and transient exponential backoff.
10. Stale processing crash recovery.
11. Localized timezone formatting across tenant timezones.
12. Multi-replica broadcast claiming and isolation.
13. MultiTenantScheduler lifecycle.
"""

import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Optional
from unittest.mock import AsyncMock, MagicMock
import pytest
import pytz
from aiogram import Bot
from aiogram.exceptions import TelegramForbiddenError, TelegramRetryAfter
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.config.settings import settings
from app.database.models.appointment import Appointment, AppointmentStatus
from app.database.models.broadcast import Broadcast, BroadcastStatus, RecipientStatus
from app.database.models.master import (
    BotInstance,
    BotInstanceStatus,
    Master,
    MasterClient,
    MasterSettings,
    MasterStatus,
    SubscriptionStatus,
)
from app.database.models.notification import (
    Notification,
    NotificationStatus,
    NotificationType,
)
from app.database.models.service import DepositType, Service
from app.database.models.user import User
from app.repositories.appointment_repository import AppointmentRepository
from app.repositories.notification_repository import NotificationRepository
from app.scheduler.jobs.hold_cleaner import clean_expired_holds
from app.scheduler.jobs.reminder_generator import generate_visit_reminders
from app.scheduler.jobs.reminder_worker import send_visit_reminders
from app.scheduler.scheduler import MultiTenantScheduler, setup_scheduler
from app.services.broadcast_service import BroadcastService
from tests.conftest import requires_postgres


import random
import pytest_asyncio
from sqlalchemy import delete


@pytest_asyncio.fixture(autouse=True)
async def cleanup_notifications(pg_engine: AsyncEngine):
    """Ensure notifications and appointments tables are clean before and after each test."""
    session_maker = async_sessionmaker(pg_engine, expire_on_commit=False)
    async with session_maker() as session:
        await session.execute(delete(Notification))
        await session.execute(delete(Appointment))
        await session.commit()
    yield
    async with session_maker() as session:
        await session.execute(delete(Notification))
        await session.execute(delete(Appointment))
        await session.commit()


def _next_telegram_id() -> int:
    return random.randint(1_000_000_000, 2_000_000_000)


async def _create_user(
    session: AsyncSession, telegram_id: Optional[int] = None, first_name: str = "TestUser"
) -> User:
    tid = _next_telegram_id()
    user = User(telegram_id=tid, first_name=first_name)
    session.add(user)
    await session.flush()
    return user


async def _create_master(
    session: AsyncSession, owner_user_id: int, display_name: str, tz_name: str = "Europe/Moscow"
) -> Master:
    master = Master(
        owner_user_id=owner_user_id,
        display_name=display_name,
        status=MasterStatus.ACTIVE,
        subscription_status=SubscriptionStatus.ACTIVE,
        timezone=tz_name,
    )
    session.add(master)
    await session.flush()
    m_settings = MasterSettings(
        master_id=master.id,
        studio_address="ул. Тестовая, 10",
        hold_duration_minutes=30,
        reminder_24h_enabled=True,
        reminder_3h_enabled=True,
    )
    session.add(m_settings)
    await session.flush()
    return master


async def _create_service(
    session: AsyncSession, master_id: int, title: str = "Маникюр", price: Decimal = Decimal("2000.00")
) -> Service:
    service = Service(
        master_id=master_id,
        title=title,
        price=price,
        duration_min=60,
        buffer_min=15,
        deposit_type=DepositType.FIXED,
        deposit_value=Decimal("500.00"),
        is_active=True,
    )
    session.add(service)
    await session.flush()
    return service


async def _create_appointment(
    session: AsyncSession,
    master_id: int,
    user_id: int,
    service: Service,
    start_time: datetime,
    status: AppointmentStatus = AppointmentStatus.WAITING_PAYMENT,
    hold_until: datetime | None = None,
) -> Appointment:
    app = Appointment(
        master_id=master_id,
        user_id=user_id,
        service_id=service.id,
        status=status,
        start_time=start_time,
        end_time=start_time + timedelta(minutes=service.duration_min),
        end_time_with_buffer=start_time + timedelta(minutes=service.duration_min + service.buffer_min),
        hold_until=hold_until,
        snapshot_service_title=service.title,
        snapshot_service_price=service.price,
        snapshot_service_duration_min=service.duration_min,
        snapshot_buffer_duration_min=service.buffer_min,
        snapshot_deposit_amount=service.deposit_value,
    )
    session.add(app)
    await session.flush()
    return app


# ---------------------------------------------------------------------------
# Test Cases
# ---------------------------------------------------------------------------

@requires_postgres
@pytest.mark.asyncio
async def test_01_hold_cleaner_concurrent_skip_locked(pg_engine: AsyncEngine) -> None:
    """Verify that multiple concurrent workers cleaning holds never process the same hold twice."""
    session_maker = async_sessionmaker(pg_engine, expire_on_commit=False)

    now_utc = datetime.now(timezone.utc)
    expired_hold = now_utc - timedelta(minutes=10)

    async with session_maker() as session:
        owner = await _create_user(session, 91001, "Owner_Hold_1")
        master = await _create_master(session, owner.id, "Master_Hold_1")
        service = await _create_service(session, master.id, "Service_Hold_1")

        created_apps = []
        for i in range(10):
            client = await _create_user(session, 92000 + i, f"Client_Hold_{i}")
            app = await _create_appointment(
                session,
                master.id,
                client.id,
                service,
                start_time=now_utc + timedelta(days=1, hours=i * 2),
                status=AppointmentStatus.WAITING_PAYMENT,
                hold_until=expired_hold,
            )
            created_apps.append(app.id)
        await session.commit()

    mock_bot = AsyncMock(spec=Bot)
    mock_bot.send_message.return_value = MagicMock()

    # Simulate two concurrent replicas running clean_expired_holds simultaneously
    worker1_task = clean_expired_holds(bot=mock_bot, session_maker=session_maker, batch_size=5)
    worker2_task = clean_expired_holds(bot=mock_bot, session_maker=session_maker, batch_size=5)

    results = await asyncio.gather(worker1_task, worker2_task)
    total_cleaned = sum(results)

    # All 10 must be expired across the two workers without collisions
    # Second sweep to pick up any remaining if batch limit was 5 each
    sweep = await clean_expired_holds(bot=mock_bot, session_maker=session_maker, batch_size=10)
    total_cleaned += sweep

    assert total_cleaned == 10

    # Verify database state
    async with session_maker() as session:
        stmt = select(Appointment).where(Appointment.id.in_(created_apps))
        res = await session.execute(stmt)
        apps = res.scalars().all()
        for a in apps:
            assert a.status == AppointmentStatus.EXPIRED
            assert a.hold_until is None


@requires_postgres
@pytest.mark.asyncio
async def test_02_hold_cleaner_protects_payment_proof_sent(pg_engine: AsyncEngine) -> None:
    """Appointments with status PAYMENT_PROOF_SENT must NEVER be expired by hold cleaner."""
    session_maker = async_sessionmaker(pg_engine, expire_on_commit=False)
    now_utc = datetime.now(timezone.utc)
    expired_hold = now_utc - timedelta(minutes=10)

    async with session_maker() as session:
        owner = await _create_user(session, 91002, "Owner_Hold_2")
        client1 = await _create_user(session, 92051, "Client_Proof")
        client2 = await _create_user(session, 92052, "Client_Waiting")
        client3 = await _create_user(session, 92053, "Client_Future")

        master = await _create_master(session, owner.id, "Master_Hold_2")
        service = await _create_service(session, master.id, "Service_Hold_2")

        # 1. PAYMENT_PROOF_SENT with past hold
        app_proof = await _create_appointment(
            session,
            master.id,
            client1.id,
            service,
            start_time=now_utc + timedelta(days=2, hours=1),
            status=AppointmentStatus.PAYMENT_PROOF_SENT,
            hold_until=expired_hold,
        )
        # 2. WAITING_PAYMENT with past hold (should expire)
        app_expired = await _create_appointment(
            session,
            master.id,
            client2.id,
            service,
            start_time=now_utc + timedelta(days=2, hours=3),
            status=AppointmentStatus.WAITING_PAYMENT,
            hold_until=expired_hold,
        )
        # 3. WAITING_PAYMENT with future hold (should NOT expire)
        app_future = await _create_appointment(
            session,
            master.id,
            client3.id,
            service,
            start_time=now_utc + timedelta(days=2, hours=5),
            status=AppointmentStatus.WAITING_PAYMENT,
            hold_until=now_utc + timedelta(minutes=20),
        )
        await session.commit()

    mock_bot = AsyncMock(spec=Bot)
    cleaned = await clean_expired_holds(bot=mock_bot, session_maker=session_maker)
    assert cleaned == 1

    async with session_maker() as session:
        re_proof = await session.get(Appointment, app_proof.id)
        assert re_proof.status == AppointmentStatus.PAYMENT_PROOF_SENT
        assert re_proof.hold_until is not None

        re_expired = await session.get(Appointment, app_expired.id)
        assert re_expired.status == AppointmentStatus.EXPIRED
        assert re_expired.hold_until is None

        re_future = await session.get(Appointment, app_future.id)
        assert re_future.status == AppointmentStatus.WAITING_PAYMENT


@requires_postgres
@pytest.mark.asyncio
async def test_03_hold_cleaner_tenant_bot_routing(pg_engine: AsyncEngine) -> None:
    """Hold cleaner routes client notification via tenant's active bot from BotRegistry."""
    session_maker = async_sessionmaker(pg_engine, expire_on_commit=False)
    now_utc = datetime.now(timezone.utc)

    async with session_maker() as session:
        owner_a = await _create_user(session, 91003, "Owner_A")
        owner_b = await _create_user(session, 91004, "Owner_B")
        client_a = await _create_user(session, 92061, "Client_A")
        client_b = await _create_user(session, 92062, "Client_B")

        master_a = await _create_master(session, owner_a.id, "Master_A")
        master_b = await _create_master(session, owner_b.id, "Master_B")
        svc_a = await _create_service(session, master_a.id, "Service_A")
        svc_b = await _create_service(session, master_b.id, "Service_B")

        app_a = await _create_appointment(
            session,
            master_a.id,
            client_a.id,
            svc_a,
            start_time=now_utc + timedelta(days=1),
            status=AppointmentStatus.WAITING_PAYMENT,
            hold_until=now_utc - timedelta(minutes=5),
        )
        app_b = await _create_appointment(
            session,
            master_b.id,
            client_b.id,
            svc_b,
            start_time=now_utc + timedelta(days=1),
            status=AppointmentStatus.WAITING_PAYMENT,
            hold_until=now_utc - timedelta(minutes=5),
        )
        await session.commit()

    bot_a = AsyncMock(spec=Bot)
    bot_b = AsyncMock(spec=Bot)

    mock_registry = MagicMock()
    async def get_bot(m_id):
        if m_id == master_a.id:
            return bot_a
        elif m_id == master_b.id:
            return bot_b
        return None
    mock_registry.get_by_master_id.side_effect = get_bot

    cleaned = await clean_expired_holds(registry=mock_registry, session_maker=session_maker)
    assert cleaned == 2

    bot_a.send_message.assert_awaited_once()
    assert bot_a.send_message.call_args.kwargs["chat_id"] == client_a.telegram_id

    bot_b.send_message.assert_awaited_once()
    assert bot_b.send_message.call_args.kwargs["chat_id"] == client_b.telegram_id


@requires_postgres
@pytest.mark.asyncio
async def test_04_hold_cleaner_notification_failure_does_not_rollback_db(pg_engine: AsyncEngine) -> None:
    """If Telegram network fails, DB state is already committed as EXPIRED."""
    session_maker = async_sessionmaker(pg_engine, expire_on_commit=False)
    now_utc = datetime.now(timezone.utc)

    async with session_maker() as session:
        owner = await _create_user(session, 91005, "Owner_Fail")
        client = await _create_user(session, 92071, "Client_Fail")
        master = await _create_master(session, owner.id, "Master_Fail")
        svc = await _create_service(session, master.id, "Service_Fail")
        app = await _create_appointment(
            session,
            master.id,
            client.id,
            svc,
            start_time=now_utc + timedelta(days=1),
            status=AppointmentStatus.WAITING_PAYMENT,
            hold_until=now_utc - timedelta(minutes=5),
        )
        await session.commit()

    mock_bot = AsyncMock(spec=Bot)
    mock_bot.send_message.side_effect = RuntimeError("Telegram network timeout")

    cleaned = await clean_expired_holds(bot=mock_bot, session_maker=session_maker)
    assert cleaned == 1

    async with session_maker() as session:
        rechecked = await session.get(Appointment, app.id)
        assert rechecked.status == AppointmentStatus.EXPIRED
        assert rechecked.hold_until is None


@requires_postgres
@pytest.mark.asyncio
async def test_05_reminder_generator_idempotency_and_concurrency(pg_engine: AsyncEngine) -> None:
    """Multiple concurrent or sequential reminder generation runs must NEVER duplicate notifications."""
    session_maker = async_sessionmaker(pg_engine, expire_on_commit=False)
    now_utc = datetime.now(timezone.utc)

    async with session_maker() as session:
        owner = await _create_user(session, 91006, "Owner_Rem_1")
        client = await _create_user(session, 92081, "Client_Rem_1")
        master = await _create_master(session, owner.id, "Master_Rem_1")
        svc = await _create_service(session, master.id, "Service_Rem_1")

        # 20 hours away -> falls into REMINDER_24H window (3h < dt <= 24h)
        app_24h = await _create_appointment(
            session,
            master.id,
            client.id,
            svc,
            start_time=now_utc + timedelta(hours=20),
            status=AppointmentStatus.CONFIRMED,
        )
        # 2 hours away -> falls into REMINDER_3H window (0h < dt <= 3h)
        app_3h = await _create_appointment(
            session,
            master.id,
            client.id,
            svc,
            start_time=now_utc + timedelta(hours=2),
            status=AppointmentStatus.CONFIRMED,
        )
        await session.commit()

    # Run two generators concurrently (multi-replica simulation)
    gen1 = generate_visit_reminders(session_maker=session_maker)
    gen2 = generate_visit_reminders(session_maker=session_maker)
    results = await asyncio.gather(gen1, gen2)
    assert sum(results) == 2

    # Second sequential run should generate 0 duplicates
    seq_run = await generate_visit_reminders(session_maker=session_maker)
    assert seq_run == 0

    async with session_maker() as session:
        stmt = select(Notification).where(
            Notification.appointment_id.in_([app_24h.id, app_3h.id])
        )
        res = await session.execute(stmt)
        notifs = list(res.scalars().all())
        assert len(notifs) == 2
        types = {n.type for n in notifs}
        assert NotificationType.REMINDER_24H in types
        assert NotificationType.REMINDER_3H in types
        for n in notifs:
            assert n.status == NotificationStatus.PENDING


@requires_postgres
@pytest.mark.asyncio
async def test_06_reminder_generator_respects_settings_and_bot_blocked(pg_engine: AsyncEngine) -> None:
    """Reminders are omitted if disabled in MasterSettings or if client blocked bot in MasterClient."""
    session_maker = async_sessionmaker(pg_engine, expire_on_commit=False)
    now_utc = datetime.now(timezone.utc)

    async with session_maker() as session:
        owner = await _create_user(session, 91007, "Owner_Rem_2")
        client_disabled = await _create_user(session, 92091, "Client_Disabled")
        client_blocked = await _create_user(session, 92092, "Client_Blocked")

        master_disabled = await _create_master(session, owner.id, "Master_Disabled")
        master_blocked = await _create_master(session, owner.id, "Master_Blocked")

        # Disable 24h reminder for master_disabled
        m_set = await session.execute(
            select(MasterSettings).where(MasterSettings.master_id == master_disabled.id)
        )
        ms = m_set.scalars().first()
        ms.reminder_24h_enabled = False

        # Mark bot as blocked for client_blocked strictly for master_blocked
        mc = MasterClient(
            master_id=master_blocked.id,
            user_id=client_blocked.id,
            is_bot_blocked=True,
            is_marketing_allowed=True,
        )
        session.add(mc)

        svc1 = await _create_service(session, master_disabled.id)
        svc2 = await _create_service(session, master_blocked.id)

        app1 = await _create_appointment(
            session,
            master_disabled.id,
            client_disabled.id,
            svc1,
            start_time=now_utc + timedelta(hours=20),
            status=AppointmentStatus.CONFIRMED,
        )
        app2 = await _create_appointment(
            session,
            master_blocked.id,
            client_blocked.id,
            svc2,
            start_time=now_utc + timedelta(hours=20),
            status=AppointmentStatus.CONFIRMED,
        )
        await session.commit()

    generated = await generate_visit_reminders(session_maker=session_maker)
    assert generated == 0

    async with session_maker() as session:
        stmt = select(Notification).where(Notification.appointment_id.in_([app1.id, app2.id]))
        res = await session.execute(stmt)
        assert len(res.scalars().all()) == 0


@requires_postgres
@pytest.mark.asyncio
async def test_07_reminder_generator_cancels_when_appointment_cancelled(pg_engine: AsyncEngine) -> None:
    """If appointment is cancelled or concluded, pending reminders are marked CANCELLED."""
    session_maker = async_sessionmaker(pg_engine, expire_on_commit=False)
    now_utc = datetime.now(timezone.utc)

    async with session_maker() as session:
        owner = await _create_user(session, 91008, "Owner_Rem_3")
        client = await _create_user(session, 92101, "Client_Rem_3")
        master = await _create_master(session, owner.id, "Master_Rem_3")
        svc = await _create_service(session, master.id, "Service_Rem_3")

        app = await _create_appointment(
            session,
            master.id,
            client.id,
            svc,
            start_time=now_utc + timedelta(hours=20),
            status=AppointmentStatus.CONFIRMED,
        )
        await session.commit()

    # Step 1: Generate pending notification
    await generate_visit_reminders(session_maker=session_maker)

    async with session_maker() as session:
        notif = (
            await session.execute(
                select(Notification).where(Notification.appointment_id == app.id)
            )
        ).scalar_one()
        assert notif.status == NotificationStatus.PENDING

        # Client cancels appointment
        re_app = await session.get(Appointment, app.id)
        re_app.status = AppointmentStatus.CANCELLED_BY_CLIENT
        await session.commit()

    # Step 2: Next generator tick cancels orphaned reminder
    await generate_visit_reminders(session_maker=session_maker)

    async with session_maker() as session:
        re_notif = await session.get(Notification, notif.id)
        assert re_notif.status == NotificationStatus.CANCELLED
        assert "no longer confirmed" in re_notif.last_error.lower()


@requires_postgres
@pytest.mark.asyncio
async def test_08_reminder_worker_concurrent_claims_skip_locked(pg_engine: AsyncEngine) -> None:
    """Concurrent worker replicas claim disjoint notifications using SKIP LOCKED with zero duplicates."""
    session_maker = async_sessionmaker(pg_engine, expire_on_commit=False)
    now_utc = datetime.now(timezone.utc)

    notif_ids = []
    async with session_maker() as session:
        owner = await _create_user(session, 91009, "Owner_Worker_1")
        master = await _create_master(session, owner.id, "Master_Worker_1")
        svc = await _create_service(session, master.id, "Service_Worker_1")

        for i in range(10):
            client = await _create_user(session, 93000 + i, f"Client_W_{i}")
            app = await _create_appointment(
                session,
                master.id,
                client.id,
                svc,
                start_time=now_utc + timedelta(days=2, hours=i * 2),
                status=AppointmentStatus.CONFIRMED,
            )
            notif = Notification(
                appointment_id=app.id,
                type=NotificationType.REMINDER_24H,
                scheduled_at=now_utc - timedelta(minutes=5),
                status=NotificationStatus.PENDING,
            )
            session.add(notif)
            await session.flush()
            notif_ids.append(notif.id)
        await session.commit()

    mock_bot = AsyncMock(spec=Bot)
    mock_bot.send_message.return_value = MagicMock()

    # Run 2 workers concurrently with small batch_size
    worker1 = send_visit_reminders(
        bot=mock_bot,
        session_maker=session_maker,
        worker_id="pod_1",
        batch_size=5,
        auto_generate=False,
    )
    worker2 = send_visit_reminders(
        bot=mock_bot,
        session_maker=session_maker,
        worker_id="pod_2",
        batch_size=5,
        auto_generate=False,
    )
    results = await asyncio.gather(worker1, worker2)
    sent_total = sum(results)

    # Sweep remaining batch if needed
    sweep = await send_visit_reminders(
        bot=mock_bot,
        session_maker=session_maker,
        worker_id="pod_sweep",
        batch_size=10,
        auto_generate=False,
    )
    sent_total += sweep

    assert sent_total == 10
    assert mock_bot.send_message.await_count == 10

    # Ensure every single notification in DB is now SENT
    async with session_maker() as session:
        stmt = select(Notification).where(Notification.id.in_(notif_ids))
        res = await session.execute(stmt)
        for n in res.scalars().all():
            assert n.status == NotificationStatus.SENT
            assert n.sent_at is not None
            assert n.attempt_count >= 1


@requires_postgres
@pytest.mark.asyncio
async def test_09_reminder_worker_telegram_forbidden_marks_bot_blocked_per_master(
    pg_engine: AsyncEngine,
) -> None:
    """403 Forbidden marks MasterClient.is_bot_blocked strictly for that master, not globally."""
    session_maker = async_sessionmaker(pg_engine, expire_on_commit=False)
    now_utc = datetime.now(timezone.utc)

    async with session_maker() as session:
        owner_a = await _create_user(session, 91010, "Owner_Forb_A")
        owner_b = await _create_user(session, 91011, "Owner_Forb_B")
        shared_user = await _create_user(session, 93101, "Shared_User")

        master_a = await _create_master(session, owner_a.id, "Master_A")
        master_b = await _create_master(session, owner_b.id, "Master_B")

        # User is client of both A and B
        mc_a = MasterClient(master_id=master_a.id, user_id=shared_user.id, is_bot_blocked=False)
        mc_b = MasterClient(master_id=master_b.id, user_id=shared_user.id, is_bot_blocked=False)
        session.add_all([mc_a, mc_b])

        svc_a = await _create_service(session, master_a.id, "Service_A")
        app_a = await _create_appointment(
            session,
            master_a.id,
            shared_user.id,
            svc_a,
            start_time=now_utc + timedelta(hours=20),
            status=AppointmentStatus.CONFIRMED,
        )
        notif_a = Notification(
            appointment_id=app_a.id,
            type=NotificationType.REMINDER_24H,
            scheduled_at=now_utc - timedelta(minutes=1),
            status=NotificationStatus.PENDING,
        )
        session.add(notif_a)
        await session.commit()
        notif_id = notif_a.id

    mock_bot = AsyncMock(spec=Bot)
    mock_bot.send_message.side_effect = TelegramForbiddenError(
        method=MagicMock(), message="Forbidden: bot was blocked by the user"
    )

    sent = await send_visit_reminders(
        bot=mock_bot, session_maker=session_maker, auto_generate=False
    )
    assert sent == 0

    async with session_maker() as session:
        n = await session.get(Notification, notif_id)
        assert n.status == NotificationStatus.FAILED
        assert "bot blocked" in n.last_error.lower()
        assert n.next_attempt_at is None

        # Check tenant isolation of bot_blocked flag
        mca = (
            await session.execute(
                select(MasterClient).where(
                    MasterClient.master_id == master_a.id,
                    MasterClient.user_id == shared_user.id,
                )
            )
        ).scalar_one()
        assert mca.is_bot_blocked is True

        mcb = (
            await session.execute(
                select(MasterClient).where(
                    MasterClient.master_id == master_b.id,
                    MasterClient.user_id == shared_user.id,
                )
            )
        ).scalar_one()
        assert mcb.is_bot_blocked is False  # Must NOT affect master B!


@requires_postgres
@pytest.mark.asyncio
async def test_10_reminder_worker_rate_limit_and_transient_retry(pg_engine: AsyncEngine) -> None:
    """TelegramRetryAfter schedules retry; transient errors apply exponential backoff."""
    session_maker = async_sessionmaker(pg_engine, expire_on_commit=False)
    now_utc = datetime.now(timezone.utc)

    async with session_maker() as session:
        owner = await _create_user(session, 91012, "Owner_Retry")
        client1 = await _create_user(session, 93201, "Client_RateLimit")
        client2 = await _create_user(session, 93202, "Client_NetworkErr")
        master = await _create_master(session, owner.id, "Master_Retry")
        svc = await _create_service(session, master.id, "Service_Retry")

        app1 = await _create_appointment(
            session, master.id, client1.id, svc, now_utc + timedelta(hours=20), AppointmentStatus.CONFIRMED
        )
        app2 = await _create_appointment(
            session, master.id, client2.id, svc, now_utc + timedelta(hours=22), AppointmentStatus.CONFIRMED
        )

        n1 = Notification(
            appointment_id=app1.id,
            type=NotificationType.REMINDER_24H,
            scheduled_at=now_utc - timedelta(minutes=1),
            status=NotificationStatus.PENDING,
        )
        n2 = Notification(
            appointment_id=app2.id,
            type=NotificationType.REMINDER_24H,
            scheduled_at=now_utc - timedelta(minutes=1),
            status=NotificationStatus.PENDING,
        )
        session.add_all([n1, n2])
        await session.commit()
        id1, id2 = n1.id, n2.id

    mock_bot = AsyncMock(spec=Bot)
    async def side_effect(chat_id, **kwargs):
        if chat_id == client1.telegram_id:
            raise TelegramRetryAfter(method=MagicMock(), message="Too Many Requests", retry_after=45)
        else:
            raise RuntimeError("Connection reset by peer")
    mock_bot.send_message.side_effect = side_effect

    sent = await send_visit_reminders(
        bot=mock_bot, session_maker=session_maker, auto_generate=False
    )
    assert sent == 0

    async with session_maker() as session:
        re1 = await session.get(Notification, id1)
        assert re1.status == NotificationStatus.PROCESSING
        assert re1.next_attempt_at is not None
        assert re1.next_attempt_at > now_utc + timedelta(seconds=40)

        re2 = await session.get(Notification, id2)
        assert re2.status == NotificationStatus.PROCESSING
        assert re2.next_attempt_at is not None
        assert "connection reset" in re2.last_error.lower()


@requires_postgres
@pytest.mark.asyncio
async def test_11_reminder_worker_stale_processing_recovery(pg_engine: AsyncEngine) -> None:
    """Worker crash recovery: stale PROCESSING notifications are reclaimed to PENDING or FAILED."""
    session_maker = async_sessionmaker(pg_engine, expire_on_commit=False)
    now_utc = datetime.now(timezone.utc)
    stale_time = now_utc - timedelta(minutes=15)

    async with session_maker() as session:
        owner = await _create_user(session, 91013, "Owner_Crash")
        client1 = await _create_user(session, 93301, "Client_Crash_1")
        client2 = await _create_user(session, 93302, "Client_Crash_2")
        master = await _create_master(session, owner.id, "Master_Crash")
        svc = await _create_service(session, master.id, "Service_Crash")

        app1 = await _create_appointment(
            session, master.id, client1.id, svc, now_utc + timedelta(hours=20), AppointmentStatus.CONFIRMED
        )
        app2 = await _create_appointment(
            session, master.id, client2.id, svc, now_utc + timedelta(hours=22), AppointmentStatus.CONFIRMED
        )

        # Stale with attempts < max_attempts (should be reclaimed to PENDING)
        n_retryable = Notification(
            appointment_id=app1.id,
            type=NotificationType.REMINDER_24H,
            scheduled_at=stale_time,
            status=NotificationStatus.PROCESSING,
            claimed_at=stale_time,
            claimed_by="crashed_pod_1",
            attempt_count=1,
        )
        # Stale with attempts >= max_attempts (should be marked FAILED)
        n_exhausted = Notification(
            appointment_id=app2.id,
            type=NotificationType.REMINDER_24H,
            scheduled_at=stale_time,
            status=NotificationStatus.PROCESSING,
            claimed_at=stale_time,
            claimed_by="crashed_pod_1",
            attempt_count=3,
        )
        session.add_all([n_retryable, n_exhausted])
        await session.commit()
        id1, id2 = n_retryable.id, n_exhausted.id

    async with session_maker() as session:
        repo = NotificationRepository(session)
        reclaimed_count = await repo.reclaim_stale_processing(timeout_seconds=300, max_attempts=3)
        await session.commit()
        assert reclaimed_count == 2

    async with session_maker() as session:
        re1 = await session.get(Notification, id1)
        assert re1.status == NotificationStatus.PENDING
        assert re1.claimed_by is None
        assert re1.claimed_at is None

        re2 = await session.get(Notification, id2)
        assert re2.status == NotificationStatus.FAILED
        assert "abandoned" in re2.last_error.lower()


@requires_postgres
@pytest.mark.asyncio
async def test_12_reminder_worker_timezone_localized_formatting(pg_engine: AsyncEngine) -> None:
    """Reminder text formats appointment date/time localized to master's timezone."""
    session_maker = async_sessionmaker(pg_engine, expire_on_commit=False)
    # 2026-10-15 12:00 UTC -> Moscow (UTC+3) is 15:00, Vienna (UTC+2 DST) is 14:00
    appt_time_utc = datetime(2026, 10, 15, 12, 0, tzinfo=timezone.utc)

    async with session_maker() as session:
        owner_msk = await _create_user(session, 91014, "Owner_MSK")
        owner_vie = await _create_user(session, 91015, "Owner_VIE")
        client_msk = await _create_user(session, 93401, "Client_MSK")
        client_vie = await _create_user(session, 93402, "Client_VIE")

        master_msk = await _create_master(session, owner_msk.id, "Master_MSK", tz_name="Europe/Moscow")
        master_vie = await _create_master(session, owner_vie.id, "Master_VIE", tz_name="Europe/Vienna")

        svc_msk = await _create_service(session, master_msk.id, "Nails MSK")
        svc_vie = await _create_service(session, master_vie.id, "Nails VIE")

        app_msk = await _create_appointment(
            session, master_msk.id, client_msk.id, svc_msk, appt_time_utc, AppointmentStatus.CONFIRMED
        )
        app_vie = await _create_appointment(
            session, master_vie.id, client_vie.id, svc_vie, appt_time_utc, AppointmentStatus.CONFIRMED
        )

        n_msk = Notification(
            appointment_id=app_msk.id,
            type=NotificationType.REMINDER_3H,
            scheduled_at=datetime.now(timezone.utc) - timedelta(minutes=1),
            status=NotificationStatus.PENDING,
        )
        n_vie = Notification(
            appointment_id=app_vie.id,
            type=NotificationType.REMINDER_3H,
            scheduled_at=datetime.now(timezone.utc) - timedelta(minutes=1),
            status=NotificationStatus.PENDING,
        )
        session.add_all([n_msk, n_vie])
        await session.commit()

    captured_texts = {}
    mock_bot = AsyncMock(spec=Bot)
    async def mock_send(chat_id, text, **kwargs):
        captured_texts[chat_id] = text

    mock_bot.send_message.side_effect = mock_send

    sent = await send_visit_reminders(
        bot=mock_bot, session_maker=session_maker, auto_generate=False
    )
    assert sent == 2

    # Moscow time check: 12:00 UTC -> 15:00
    assert "15:00" in captured_texts[client_msk.telegram_id]
    # Vienna time check: 12:00 UTC -> 14:00 (October is CEST)
    assert "14:00" in captured_texts[client_vie.telegram_id]


@requires_postgres
@pytest.mark.asyncio
async def test_13_broadcast_service_concurrent_claiming_skip_locked(pg_engine: AsyncEngine) -> None:
    """Multiple workers executing broadcast claim recipients with SKIP LOCKED and zero duplicate sends."""
    session_maker = async_sessionmaker(pg_engine, expire_on_commit=False)

    async with session_maker() as session:
        owner = await _create_user(session, 91016, "Owner_Bcast")
        master = await _create_master(session, owner.id, "Master_Bcast")

        for i in range(10):
            c = await _create_user(session, 94000 + i, f"Client_Bcast_{i}")
            mc = MasterClient(
                master_id=master.id,
                user_id=c.id,
                is_marketing_allowed=True,
                is_bot_blocked=False,
            )
            session.add(mc)
        await session.commit()

        svc = BroadcastService(session)
        broadcast = await svc.create_broadcast(
            master_id=master.id,
            text="Тестовая акция для всех клиентов!",
        )
        b_id = broadcast.id
        await session.commit()

    mock_bot = AsyncMock(spec=Bot)
    mock_bot.send_message.return_value = MagicMock()

    # Simulate two worker instances executing the broadcast concurrently
    async def run_worker(w_id):
        async with session_maker() as session:
            b_svc = BroadcastService(session)
            await b_svc.execute_broadcast(
                master_id=master.id,
                broadcast_id=b_id,
                bot=mock_bot,
                worker_id=w_id,
                batch_size=5,
            )

    await asyncio.gather(run_worker("b_worker_1"), run_worker("b_worker_2"))

    assert mock_bot.send_message.await_count == 10

    async with session_maker() as session:
        b = await session.get(Broadcast, b_id)
        assert b.status == BroadcastStatus.COMPLETED
        assert b.success_count == 10
        assert b.fail_count == 0
        assert b.finished_at is not None


@requires_postgres
@pytest.mark.asyncio
async def test_14_broadcast_service_handles_bot_blocked_correctly(pg_engine: AsyncEngine) -> None:
    """Broadcast handles TelegramForbiddenError and marks user bot_blocked strictly for master."""
    session_maker = async_sessionmaker(pg_engine, expire_on_commit=False)

    async with session_maker() as session:
        owner = await _create_user(session, 91017, "Owner_Bcast_2")
        master = await _create_master(session, owner.id, "Master_Bcast_2")

        c1 = await _create_user(session, 94101, "Client_Ok")
        c2 = await _create_user(session, 94102, "Client_Blocked")

        mc1 = MasterClient(master_id=master.id, user_id=c1.id, is_marketing_allowed=True, is_bot_blocked=False)
        mc2 = MasterClient(master_id=master.id, user_id=c2.id, is_marketing_allowed=True, is_bot_blocked=False)
        session.add_all([mc1, mc2])
        await session.commit()

        svc = BroadcastService(session)
        broadcast = await svc.create_broadcast(master_id=master.id, text="Новое предложение")
        b_id = broadcast.id
        await session.commit()

    mock_bot = AsyncMock(spec=Bot)
    async def send_msg(chat_id, text, **kwargs):
        if chat_id == c2.telegram_id:
            raise TelegramForbiddenError(method=MagicMock(), message="Forbidden: bot was blocked by the user")
        return MagicMock()
    mock_bot.send_message.side_effect = send_msg

    async with session_maker() as session:
        b_svc = BroadcastService(session)
        res = await b_svc.execute_broadcast(
            master_id=master.id,
            broadcast_id=b_id,
            bot=mock_bot,
        )
        assert res.status == BroadcastStatus.COMPLETED
        assert res.success_count == 1
        assert res.fail_count == 1

        mc2_re = (
            await session.execute(
                select(MasterClient).where(
                    MasterClient.master_id == master.id,
                    MasterClient.user_id == c2.id,
                )
            )
        ).scalar_one()
        assert mc2_re.is_bot_blocked is True


@pytest.mark.asyncio
async def test_15_multitenant_scheduler_lifecycle() -> None:
    """Verify MultiTenantScheduler initialization, configuration and clean shutdown."""
    scheduler = setup_scheduler()
    assert scheduler.running is False

    scheduler.start()
    assert scheduler.running is True

    scheduler.shutdown(wait=False)
    assert scheduler.running is False
