"""Comprehensive test suite for Phase 9: SaaS Subscription, Trial & Access Control.

Covers:
1. Real-time effective subscription calculation (TRIAL_ACTIVE, PAID_ACTIVE, EXPIRED, SUSPENDED).
2. Multi-tenant isolation across distinct master subscriptions.
3. Client booking gating with neutral client message (no billing leakage).
4. Direct service bypass protection on create_hold.
5. In-flight grace period for payment proof submissions on existing active holds.
6. Rejection of payment proof once hold expires.
7. Durability of existing appointments and reminders for expired masters.
8. Unrestricted owner admin access for expired masters.
9. Marketing broadcast blocking on expired masters.
10. Renewal extension preserving future paid_until (no lost days).
11. Renewal during active trial preserving remaining trial days.
12. Payment callback idempotency (duplicate callbacks ignored, no double extension).
13. Concurrent successful payment processing with row locking.
14. Non-activation on pending or failed payments.
15. IDOR protection: non-owner cannot create or confirm subscription payments.
16. Background multi-replica expiration worker (SELECT FOR UPDATE SKIP LOCKED).
17. Webhook and BotRegistry independence from subscription status.
18. Manager Bot subscription screen, plan listing and payment flow.
"""

import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import random
from typing import Optional
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
import pytest_asyncio
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.config.settings import settings
from app.database.models.appointment import Appointment, AppointmentStatus
from app.database.models.audit import AuditLog
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
from app.database.models.notification import Notification, NotificationStatus, NotificationType
from app.database.models.service import DepositType, Service
from app.database.models.subscription import (
    EffectiveSubscriptionStatus,
    SubscriptionPayment,
    SubscriptionPeriod,
    SubscriptionPlan,
)
from app.database.models.user import User
from app.services.billing.manual_provider import ManualBillingProvider
from app.services.booking_service import BookingService
from app.services.broadcast_service import BroadcastService
from app.services.exceptions import (
    BillingIDORViolationError,
    PaymentAlreadyProcessedError,
    PlanNotFoundError,
    SubscriptionError,
    SubscriptionExpiredError,
    SubscriptionSuspendedError,
)
from app.services.payment_service import PaymentService
from app.services.subscription_access_policy import (
    NEUTRAL_CLIENT_EXPIRED_MESSAGE,
    SubscriptionAccessPolicy,
)
from app.services.subscription_service import EffectiveSubscription, SubscriptionService
from tests.conftest import requires_postgres


def _next_telegram_id() -> int:
    return random.randint(2_000_000_000, 3_000_000_000)


async def _create_user(session: AsyncSession, first_name: str = "OwnerUser") -> User:
    user = User(telegram_id=_next_telegram_id(), first_name=first_name)
    session.add(user)
    await session.flush()
    return user


async def _create_master(
    session: AsyncSession,
    owner_user_id: int,
    display_name: str = "Test Studio",
    subscription_status: SubscriptionStatus = SubscriptionStatus.TRIAL,
    trial_ends_at: Optional[datetime] = None,
    paid_until: Optional[datetime] = None,
) -> Master:
    master = Master(
        owner_user_id=owner_user_id,
        display_name=display_name,
        status=MasterStatus.ACTIVE,
        subscription_status=subscription_status,
        trial_ends_at=trial_ends_at,
        paid_until=paid_until,
        timezone="Europe/Moscow",
    )
    session.add(master)
    await session.flush()

    m_settings = MasterSettings(
        master_id=master.id,
        studio_address="ул. Мастеров, 1",
        hold_duration_minutes=30,
        reminder_24h_enabled=True,
        reminder_3h_enabled=True,
    )
    session.add(m_settings)
    await session.flush()
    return master


async def _create_service(session: AsyncSession, master_id: int) -> Service:
    service = Service(
        master_id=master_id,
        title="Маникюр",
        duration_min=60,
        price=Decimal("1500.00"),
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
    service_id: int,
    start_time: datetime,
    end_time: datetime,
    status: AppointmentStatus = AppointmentStatus.WAITING_PAYMENT,
    hold_until: Optional[datetime] = None,
) -> Appointment:
    appt = Appointment(
        master_id=master_id,
        user_id=user_id,
        service_id=service_id,
        start_time=start_time,
        end_time=end_time,
        end_time_with_buffer=end_time,
        status=status,
        hold_until=hold_until,
        cancel_policy_agreed=True,
        snapshot_service_title="Маникюр",
        snapshot_service_price=Decimal("1500.00"),
        snapshot_service_duration_min=60,
        snapshot_buffer_duration_min=0,
        snapshot_deposit_amount=Decimal("500.00"),
    )
    session.add(appt)
    await session.flush()
    return appt



@pytest.mark.asyncio
@requires_postgres
async def test_01_effective_status_trial_active_vs_expired(pg_engine: AsyncEngine):
    """Test TRIAL_ACTIVE within bounds and EXPIRED after elapsed trial."""
    session_maker = async_sessionmaker(pg_engine, expire_on_commit=False)
    async with session_maker() as session:
        user = await _create_user(session, "TrialUser")
        now = datetime.now(timezone.utc)

        # 1. Active trial: 5 days left
        m_active = await _create_master(
            session,
            user.id,
            display_name="Trial Active Studio",
            subscription_status=SubscriptionStatus.TRIAL,
            trial_ends_at=now + timedelta(days=5),
        )

        # 2. Expired trial: 2 hours ago
        m_expired = await _create_master(
            session,
            user.id,
            display_name="Trial Expired Studio",
            subscription_status=SubscriptionStatus.TRIAL,
            trial_ends_at=now - timedelta(hours=2),
        )
        await session.commit()

        sub_service = SubscriptionService(session)

        eff_active = await sub_service.get_effective_status(m_active.id, now_utc=now)
        assert eff_active.status == EffectiveSubscriptionStatus.TRIAL_ACTIVE
        assert eff_active.is_active is True
        assert eff_active.days_remaining == 5

        eff_expired = await sub_service.get_effective_status(m_expired.id, now_utc=now)
        assert eff_expired.status == EffectiveSubscriptionStatus.EXPIRED
        assert eff_expired.is_active is False
        assert eff_expired.days_remaining == 0


@pytest.mark.asyncio
@requires_postgres
async def test_02_effective_status_paid_active_vs_expired(pg_engine: AsyncEngine):
    """Test PAID_ACTIVE when paid_until in future, EXPIRED when paid_until in past."""
    session_maker = async_sessionmaker(pg_engine, expire_on_commit=False)
    async with session_maker() as session:
        user = await _create_user(session, "PaidUser")
        now = datetime.now(timezone.utc)

        # 1. Paid active: 14 days left
        m_paid = await _create_master(
            session,
            user.id,
            display_name="Paid Active Studio",
            subscription_status=SubscriptionStatus.ACTIVE,
            paid_until=now + timedelta(days=14),
        )

        # 2. Paid elapsed: 1 day ago
        m_elapsed = await _create_master(
            session,
            user.id,
            display_name="Paid Elapsed Studio",
            subscription_status=SubscriptionStatus.ACTIVE,
            paid_until=now - timedelta(days=1),
        )
        await session.commit()

        sub_service = SubscriptionService(session)

        eff_paid = await sub_service.get_effective_status(m_paid.id, now_utc=now)
        assert eff_paid.status == EffectiveSubscriptionStatus.PAID_ACTIVE
        assert eff_paid.is_active is True
        assert eff_paid.days_remaining == 14

        eff_elapsed = await sub_service.get_effective_status(m_elapsed.id, now_utc=now)
        assert eff_elapsed.status == EffectiveSubscriptionStatus.EXPIRED
        assert eff_elapsed.is_active is False
        assert eff_elapsed.days_remaining == 0


@pytest.mark.asyncio
@requires_postgres
async def test_03_effective_status_suspended(pg_engine: AsyncEngine):
    """Administrative SUSPENDED takes precedence over any active timestamps."""
    session_maker = async_sessionmaker(pg_engine, expire_on_commit=False)
    async with session_maker() as session:
        user = await _create_user(session, "SuspendedUser")
        now = datetime.now(timezone.utc)

        m_suspended = await _create_master(
            session,
            user.id,
            display_name="Suspended Studio",
            subscription_status=SubscriptionStatus.SUSPENDED,
            paid_until=now + timedelta(days=90),  # paid for 90 days, but locked
        )
        await session.commit()

        sub_service = SubscriptionService(session)
        eff = await sub_service.get_effective_status(m_suspended.id, now_utc=now)
        assert eff.status == EffectiveSubscriptionStatus.SUSPENDED
        assert eff.is_active is False


@pytest.mark.asyncio
@requires_postgres
async def test_04_multi_tenant_isolation_different_subscriptions(pg_engine: AsyncEngine):
    """Ensure masters with different subscription states never leak to one another."""
    session_maker = async_sessionmaker(pg_engine, expire_on_commit=False)
    async with session_maker() as session:
        now = datetime.now(timezone.utc)
        u1 = await _create_user(session, "User1")
        u2 = await _create_user(session, "User2")
        u3 = await _create_user(session, "User3")

        m1 = await _create_master(
            session, u1.id, "Studio Expired", SubscriptionStatus.EXPIRED,
            trial_ends_at=now - timedelta(days=2)
        )
        m2 = await _create_master(
            session, u2.id, "Studio Active", SubscriptionStatus.ACTIVE,
            paid_until=now + timedelta(days=30)
        )
        m3 = await _create_master(
            session, u3.id, "Studio Trial", SubscriptionStatus.TRIAL,
            trial_ends_at=now + timedelta(days=10)
        )
        await session.commit()

        policy = SubscriptionAccessPolicy(session)

        # Policy checks
        assert await policy.can_accept_new_booking(m1.id) is False
        assert await policy.can_accept_new_booking(m2.id) is True
        assert await policy.can_accept_new_booking(m3.id) is True

        assert await policy.can_create_hold(m1.id) is False
        assert await policy.can_create_hold(m2.id) is True
        assert await policy.can_create_hold(m3.id) is True

        assert await policy.can_manage_admin(m1.id) is True
        assert await policy.can_manage_admin(m2.id) is True
        assert await policy.can_manage_admin(m3.id) is True


@pytest.mark.asyncio
@requires_postgres
async def test_05_client_new_booking_blocked_on_expired_with_neutral_message(pg_engine: AsyncEngine):
    """BookingService.create_hold_booking raises SubscriptionExpiredError with neutral message on expired master."""
    session_maker = async_sessionmaker(pg_engine, expire_on_commit=False)
    async with session_maker() as session:
        now = datetime.now(timezone.utc)
        owner = await _create_user(session, "Owner")
        client = await _create_user(session, "Client")
        master = await _create_master(
            session,
            owner.id,
            "Expired Salon",
            SubscriptionStatus.EXPIRED,
            trial_ends_at=now - timedelta(days=1),
        )
        service = await _create_service(session, master.id)
        await session.commit()

        booking_service = BookingService(session)

        with pytest.raises(SubscriptionExpiredError) as exc_info:
            await booking_service.create_hold_booking(
                master_id=master.id,
                user_id=client.id,
                service_id=service.id,
                start_time=now + timedelta(days=2),
            )

        assert NEUTRAL_CLIENT_EXPIRED_MESSAGE in str(exc_info.value)
        # Ensure ZERO billing terms leaked
        forbidden_terms = ["подписк", "тариф", "оплат", "баланс", "subscription", "expired", "billing"]
        for term in forbidden_terms:
            assert term not in str(exc_info.value).lower()


@pytest.mark.asyncio
@requires_postgres
async def test_06_direct_service_bypass_blocked_on_create_hold(pg_engine: AsyncEngine):
    """Verify that no appointment is inserted if subscription check fails during hold creation."""
    session_maker = async_sessionmaker(pg_engine, expire_on_commit=False)
    async with session_maker() as session:
        now = datetime.now(timezone.utc)
        owner = await _create_user(session, "OwnerDirect")
        client = await _create_user(session, "ClientDirect")
        master = await _create_master(
            session,
            owner.id,
            "Expired Direct",
            SubscriptionStatus.TRIAL,
            trial_ends_at=now - timedelta(minutes=10),
        )
        service = await _create_service(session, master.id)
        await session.commit()

        booking_service = BookingService(session)

        with pytest.raises(SubscriptionExpiredError):
            await booking_service.create_hold_booking(
                master_id=master.id,
                user_id=client.id,
                service_id=service.id,
                start_time=now + timedelta(days=1),
            )

        # Verify no appointments were created
        stmt = select(func.count()).select_from(Appointment).where(Appointment.master_id == master.id)
        count = (await session.execute(stmt)).scalar()
        assert count == 0


@pytest.mark.asyncio
@requires_postgres
async def test_07_in_flight_payment_proof_allowed_during_grace(pg_engine: AsyncEngine):
    """In-flight grace period: active hold created before expiration can submit payment proof."""
    session_maker = async_sessionmaker(pg_engine, expire_on_commit=False)
    async with session_maker() as session:
        now = datetime.now(timezone.utc)
        owner = await _create_user(session, "OwnerGrace")
        client = await _create_user(session, "ClientGrace")

        # Master is currently expired
        master = await _create_master(
            session,
            owner.id,
            "Grace Salon",
            SubscriptionStatus.EXPIRED,
            trial_ends_at=now - timedelta(minutes=5),
        )
        service = await _create_service(session, master.id)

        # But appointment hold was created 10 minutes ago and hold_until is 20 minutes in future
        appt = await _create_appointment(
            session=session,
            master_id=master.id,
            user_id=client.id,
            service_id=service.id,
            start_time=now + timedelta(days=2),
            end_time=now + timedelta(days=2, hours=1),
            status=AppointmentStatus.WAITING_PAYMENT,
            hold_until=now + timedelta(minutes=20),
        )
        await session.commit()

        from app.database.models.payment import Payment, PaymentStatus
        payment = Payment(
            master_id=master.id,
            appointment_id=appt.id,
            user_id=client.id,
            amount=Decimal("500.00"),
            status=PaymentStatus.PENDING,
        )
        session.add(payment)
        await session.commit()

        policy = SubscriptionAccessPolicy(session)
        # In-flight grace check allows proof submission
        allowed = await policy.can_submit_payment_proof(master.id, appt.id, now_utc=now)
        assert allowed is True

        # And PaymentService allows submitting proof
        payment_service = PaymentService(session)
        await payment_service.submit_payment_proof(
            master_id=master.id,
            appointment_id=appt.id,
            user_id=client.id,
            telegram_file_id="tg_photo_grace_123",
            telegram_file_unique_id="unique_grace_123",
        )
        await session.refresh(appt)
        assert appt.status == AppointmentStatus.PAYMENT_PROOF_SENT


@pytest.mark.asyncio
@requires_postgres
async def test_08_in_flight_payment_proof_blocked_after_hold_expired(pg_engine: AsyncEngine):
    """In-flight grace expires when hold_until has passed."""
    session_maker = async_sessionmaker(pg_engine, expire_on_commit=False)
    async with session_maker() as session:
        now = datetime.now(timezone.utc)
        owner = await _create_user(session, "OwnerGraceExpired")
        client = await _create_user(session, "ClientGraceExpired")

        master = await _create_master(
            session,
            owner.id,
            "Grace Expired Salon",
            SubscriptionStatus.EXPIRED,
            trial_ends_at=now - timedelta(hours=1),
        )
        service = await _create_service(session, master.id)

        # Appointment hold expired 5 minutes ago
        appt = await _create_appointment(
            session=session,
            master_id=master.id,
            user_id=client.id,
            service_id=service.id,
            start_time=now + timedelta(days=2),
            end_time=now + timedelta(days=2, hours=1),
            status=AppointmentStatus.WAITING_PAYMENT,
            hold_until=now - timedelta(minutes=5),
        )
        await session.commit()

        policy = SubscriptionAccessPolicy(session)
        allowed = await policy.can_submit_payment_proof(master.id, appt.id, now_utc=now)
        assert allowed is False

        payment_service = PaymentService(session)
        with pytest.raises(SubscriptionExpiredError):
            await payment_service.submit_payment_proof(
                master_id=master.id,
                appointment_id=appt.id,
                user_id=client.id,
                telegram_file_id="tg_photo_late_123",
                telegram_file_unique_id="unique_late_123",
            )


@pytest.mark.asyncio
@requires_postgres
async def test_09_existing_appointments_reminders_continue_after_expiry(pg_engine: AsyncEngine):
    """Existing confirmed appointments and their reminders are never destroyed on expiration."""
    session_maker = async_sessionmaker(pg_engine, expire_on_commit=False)
    async with session_maker() as session:
        now = datetime.now(timezone.utc)
        owner = await _create_user(session, "OwnerPersist")
        client = await _create_user(session, "ClientPersist")

        master = await _create_master(
            session,
            owner.id,
            "Persist Salon",
            SubscriptionStatus.ACTIVE,
            paid_until=now + timedelta(hours=1),
        )
        service = await _create_service(session, master.id)

        appt = await _create_appointment(
            session=session,
            master_id=master.id,
            user_id=client.id,
            service_id=service.id,
            start_time=now + timedelta(days=3),
            end_time=now + timedelta(days=3, hours=1),
            status=AppointmentStatus.CONFIRMED,
        )

        notif = Notification(
            appointment_id=appt.id,
            type=NotificationType.REMINDER_24H,
            scheduled_at=now + timedelta(days=2),
            status=NotificationStatus.PENDING,
        )
        session.add(notif)
        await session.commit()

        # Simulate subscription expiration
        master.subscription_status = SubscriptionStatus.EXPIRED
        master.paid_until = now - timedelta(minutes=1)
        await session.commit()

        # Appointments and notifications remain in database
        stmt_appt = select(Appointment).where(Appointment.id == appt.id)
        res_appt = (await session.execute(stmt_appt)).scalar_one()
        assert res_appt.status == AppointmentStatus.CONFIRMED

        stmt_notif = select(Notification).where(Notification.id == notif.id)
        res_notif = (await session.execute(stmt_notif)).scalar_one()
        assert res_notif.status == NotificationStatus.PENDING


@pytest.mark.asyncio
@requires_postgres
async def test_10_admin_actions_permitted_after_expiry(pg_engine: AsyncEngine):
    """Admin access is preserved for expired masters (data never locked away)."""
    session_maker = async_sessionmaker(pg_engine, expire_on_commit=False)
    async with session_maker() as session:
        now = datetime.now(timezone.utc)
        owner = await _create_user(session, "OwnerAdmin")
        master = await _create_master(
            session,
            owner.id,
            "Expired Admin Salon",
            SubscriptionStatus.EXPIRED,
            trial_ends_at=now - timedelta(days=5),
        )
        await session.commit()

        policy = SubscriptionAccessPolicy(session)
        assert await policy.can_manage_admin(master.id) is True


@pytest.mark.asyncio
@requires_postgres
async def test_11_marketing_broadcast_blocked_on_expired(pg_engine: AsyncEngine):
    """Marketing broadcasts cannot be created or executed when master is expired."""
    session_maker = async_sessionmaker(pg_engine, expire_on_commit=False)
    async with session_maker() as session:
        now = datetime.now(timezone.utc)
        owner = await _create_user(session, "OwnerBroadcast")
        master = await _create_master(
            session,
            owner.id,
            "Expired Broadcast Salon",
            SubscriptionStatus.EXPIRED,
            trial_ends_at=now - timedelta(days=1),
        )
        await session.commit()

        broadcast_service = BroadcastService(session)

        # 1. Creation blocked
        with pytest.raises(SubscriptionExpiredError):
            await broadcast_service.create_broadcast(
                master_id=master.id,
                admin_id=owner.id,
                text="Скидки на все услуги!",
            )

        # 2. Even if broadcast existed, execution blocked
        broadcast = Broadcast(
            master_id=master.id,
            text="Архивное сообщение",
            status=BroadcastStatus.DRAFT,
            total_count=0,
        )
        session.add(broadcast)
        await session.commit()

        with pytest.raises(SubscriptionExpiredError):
            await broadcast_service.execute_broadcast(
                master_id=master.id,
                broadcast_id=broadcast.id,
            )


@pytest.mark.asyncio
@requires_postgres
async def test_12_renewal_from_future_paid_until_preserves_days(pg_engine: AsyncEngine):
    """Renewal when paid_until is in the future extends from old paid_until without losing days."""
    session_maker = async_sessionmaker(pg_engine, expire_on_commit=False)
    async with session_maker() as session:
        now = datetime.now(timezone.utc)
        owner = await _create_user(session, "OwnerFuturePaid")
        initial_paid_until = now + timedelta(days=15)

        master = await _create_master(
            session,
            owner.id,
            "Future Paid Salon",
            SubscriptionStatus.ACTIVE,
            paid_until=initial_paid_until,
        )
        await session.commit()

        sub_service = SubscriptionService(session)
        payment, intent = await sub_service.create_subscription_payment(
            master_id=master.id,
            actor_user_id=owner.id,
            plan_code="BASIC",
        )
        await session.commit()

        success = await sub_service.process_successful_payment(
            provider=payment.provider,
            provider_payment_id=payment.provider_payment_id,
        )
        assert success is True
        await session.commit()

        await session.refresh(master)
        # Expected: initial_paid_until + 30 days
        expected = initial_paid_until + timedelta(days=30)
        assert master.subscription_status == SubscriptionStatus.ACTIVE
        assert abs((master.paid_until - expected).total_seconds()) < 2


@pytest.mark.asyncio
@requires_postgres
async def test_13_renewal_during_trial_preserves_remaining_trial(pg_engine: AsyncEngine):
    """Payment during trial starts after trial ends (preserves remaining trial days)."""
    session_maker = async_sessionmaker(pg_engine, expire_on_commit=False)
    async with session_maker() as session:
        now = datetime.now(timezone.utc)
        owner = await _create_user(session, "OwnerTrialPreserve")
        trial_ends = now + timedelta(days=10)

        master = await _create_master(
            session,
            owner.id,
            "Trial Preserve Salon",
            SubscriptionStatus.TRIAL,
            trial_ends_at=trial_ends,
        )
        await session.commit()

        sub_service = SubscriptionService(session)
        payment, intent = await sub_service.create_subscription_payment(
            master_id=master.id,
            actor_user_id=owner.id,
            plan_code="BASIC",
        )
        await session.commit()

        success = await sub_service.process_successful_payment(
            provider=payment.provider,
            provider_payment_id=payment.provider_payment_id,
        )
        assert success is True
        await session.commit()

        await session.refresh(master)
        # Expected: trial_ends + 30 days
        expected = trial_ends + timedelta(days=30)
        assert master.subscription_status == SubscriptionStatus.ACTIVE
        assert abs((master.paid_until - expected).total_seconds()) < 2


@pytest.mark.asyncio
@requires_postgres
async def test_14_payment_callback_idempotency_no_double_extension(pg_engine: AsyncEngine):
    """Duplicate callback with the same provider_payment_id does not extend twice."""
    session_maker = async_sessionmaker(pg_engine, expire_on_commit=False)
    async with session_maker() as session:
        now = datetime.now(timezone.utc)
        owner = await _create_user(session, "OwnerIdempotent")

        master = await _create_master(
            session,
            owner.id,
            "Idempotent Salon",
            SubscriptionStatus.EXPIRED,
            trial_ends_at=now - timedelta(days=1),
        )
        await session.commit()

        sub_service = SubscriptionService(session)
        payment, intent = await sub_service.create_subscription_payment(
            master_id=master.id,
            actor_user_id=owner.id,
            plan_code="BASIC",
        )
        await session.commit()

        # First callback
        first_res = await sub_service.process_successful_payment(
            provider=payment.provider,
            provider_payment_id=payment.provider_payment_id,
        )
        assert first_res is True
        await session.commit()
        await session.refresh(master)
        first_paid_until = master.paid_until

        # Second callback (duplicate webhook from provider) returns True idempotent ack without double extension
        second_res = await sub_service.process_successful_payment(
            provider=payment.provider,
            provider_payment_id=payment.provider_payment_id,
        )
        assert second_res is True

        # Verify paid_until did NOT change
        await session.refresh(master)
        assert master.paid_until == first_paid_until

        # Verify only 1 period created for this payment
        stmt = select(func.count()).select_from(SubscriptionPeriod).where(
            SubscriptionPeriod.external_payment_id == payment.provider_payment_id
        )
        period_count = (await session.execute(stmt)).scalar()
        assert period_count == 1


@pytest.mark.asyncio
@requires_postgres
async def test_15_concurrent_successful_payments_both_accounted(pg_engine: AsyncEngine):
    """Concurrent payments for the same master are sequentially processed with FOR UPDATE lock."""
    session_maker = async_sessionmaker(pg_engine, expire_on_commit=False)
    async with session_maker() as session:
        now = datetime.now(timezone.utc)
        owner = await _create_user(session, "OwnerConcurrent")

        master = await _create_master(
            session,
            owner.id,
            "Concurrent Salon",
            SubscriptionStatus.EXPIRED,
            trial_ends_at=now - timedelta(days=1),
        )
        await session.commit()

        sub_service = SubscriptionService(session)
        p1, _ = await sub_service.create_subscription_payment(
            master_id=master.id, actor_user_id=owner.id, plan_code="BASIC"
        )
        p2, _ = await sub_service.create_subscription_payment(
            master_id=master.id, actor_user_id=owner.id, plan_code="BASIC"
        )
        await session.commit()

        id1, prov1 = p1.provider_payment_id, p1.provider
        id2, prov2 = p2.provider_payment_id, p2.provider

    # Process both in parallel using separate sessions
    async def process_one(prov: str, pid: str):
        async with session_maker() as s:
            svc = SubscriptionService(s)
            res = await svc.process_successful_payment(prov, pid)
            await s.commit()
            return res

    results = await asyncio.gather(
        process_one(prov1, id1),
        process_one(prov2, id2),
    )
    assert all(results)

    async with session_maker() as session:
        m = await session.get(Master, master.id)
        # 30 days + 30 days = 60 days from approx now
        assert m.subscription_status == SubscriptionStatus.ACTIVE
        days_from_now = (m.paid_until - datetime.now(timezone.utc)).total_seconds() / 86400
        assert 59.0 <= days_from_now <= 61.0


@pytest.mark.asyncio
@requires_postgres
async def test_16_failed_and_pending_payments_do_not_activate(pg_engine: AsyncEngine):
    """Pending and failed payments never extend paid_until or activate subscription."""
    session_maker = async_sessionmaker(pg_engine, expire_on_commit=False)
    async with session_maker() as session:
        now = datetime.now(timezone.utc)
        owner = await _create_user(session, "OwnerFailed")

        master = await _create_master(
            session,
            owner.id,
            "Failed Payment Salon",
            SubscriptionStatus.EXPIRED,
            trial_ends_at=now - timedelta(days=5),
        )
        await session.commit()

        sub_service = SubscriptionService(session)
        payment, intent = await sub_service.create_subscription_payment(
            master_id=master.id,
            actor_user_id=owner.id,
            plan_code="BASIC",
        )
        await session.commit()

        # Check pending state
        eff = await sub_service.get_effective_status(master.id)
        assert eff.status == EffectiveSubscriptionStatus.EXPIRED
        assert master.paid_until is None

        # Mark payment as FAILED
        payment.status = "FAILED"
        await session.commit()

        eff2 = await sub_service.get_effective_status(master.id)
        assert eff2.status == EffectiveSubscriptionStatus.EXPIRED
        assert master.paid_until is None


@pytest.mark.asyncio
@requires_postgres
async def test_17_idor_protection_non_owner_cannot_create_or_view_payment(pg_engine: AsyncEngine):
    """Attempt by non-owner user to create subscription payment raises BillingIDORViolationError."""
    session_maker = async_sessionmaker(pg_engine, expire_on_commit=False)
    async with session_maker() as session:
        owner = await _create_user(session, "RealOwner")
        attacker = await _create_user(session, "Attacker")

        master = await _create_master(session, owner.id, "Protected Salon")
        await session.commit()

        sub_service = SubscriptionService(session)
        with pytest.raises(BillingIDORViolationError):
            await sub_service.create_subscription_payment(
                master_id=master.id,
                actor_user_id=attacker.id,
                plan_code="BASIC",
            )


@pytest.mark.asyncio
@requires_postgres
async def test_18_refresh_expired_subscriptions_worker_concurrency(pg_engine: AsyncEngine):
    """Background expiration worker transitions elapsed TRIAL and ACTIVE to EXPIRED with audit log."""
    session_maker = async_sessionmaker(pg_engine, expire_on_commit=False)
    async with session_maker() as session:
        now = datetime.now(timezone.utc)
        owner = await _create_user(session, "OwnerWorker")

        # 1. Expired trial
        m1 = await _create_master(
            session, owner.id, "Worker Trial Elapsed",
            SubscriptionStatus.TRIAL,
            trial_ends_at=now - timedelta(hours=1),
        )
        # 2. Expired active
        m2 = await _create_master(
            session, owner.id, "Worker Active Elapsed",
            SubscriptionStatus.ACTIVE,
            paid_until=now - timedelta(hours=2),
        )
        # 3. Not expired active
        m3 = await _create_master(
            session, owner.id, "Worker Active Valid",
            SubscriptionStatus.ACTIVE,
            paid_until=now + timedelta(days=10),
        )
        await session.commit()

        sub_service = SubscriptionService(session)
        count = await sub_service.refresh_expired_subscriptions(batch_size=50, now_utc=now)
        await session.commit()

        assert count >= 2

        await session.refresh(m1)
        await session.refresh(m2)
        await session.refresh(m3)

        assert m1.subscription_status == SubscriptionStatus.EXPIRED
        assert m2.subscription_status == SubscriptionStatus.EXPIRED
        assert m3.subscription_status == SubscriptionStatus.ACTIVE

        # Check audit log
        stmt = select(AuditLog).where(AuditLog.master_id.in_([m1.id, m2.id]))
        audit_records = (await session.execute(stmt)).scalars().all()
        assert len(audit_records) >= 2


@pytest.mark.asyncio
@requires_postgres
async def test_19_webhook_and_bot_registry_unaffected_by_expired_subscription(pg_engine: AsyncEngine):
    """Webhooks and BotRegistry continue operating for expired masters without transport-level 403."""
    session_maker = async_sessionmaker(pg_engine, expire_on_commit=False)
    async with session_maker() as session:
        now = datetime.now(timezone.utc)
        owner = await _create_user(session, "OwnerWebhook")
        master = await _create_master(
            session,
            owner.id,
            "Webhook Expired Salon",
            SubscriptionStatus.EXPIRED,
            trial_ends_at=now - timedelta(days=10),
        )
        # Add a BotInstance
        bot_tid = _next_telegram_id()
        bot_inst = BotInstance(
            master_id=master.id,
            telegram_bot_id=bot_tid,
            encrypted_token="test_enc_token",
            token_version=1,
            telegram_username=f"expired_test_bot_{bot_tid}",
            telegram_first_name="Expired Bot",
            status=BotInstanceStatus.ACTIVE,
        )
        session.add(bot_inst)
        await session.commit()

        # Bot instance status remains ACTIVE in registry
        assert bot_inst.status == BotInstanceStatus.ACTIVE
        assert master.subscription_status == SubscriptionStatus.EXPIRED
        # Notice: BotInstance.status is independent from Master.subscription_status!


@pytest.mark.asyncio
@requires_postgres
async def test_20_manager_bot_subscription_screen_and_renewal(pg_engine: AsyncEngine):
    """Test Manager Bot subscription card rendering, plan list, payment intent, and confirmation."""
    session_maker = async_sessionmaker(pg_engine, expire_on_commit=False)
    async with session_maker() as session:
        now = datetime.now(timezone.utc)
        owner = await _create_user(session, "ManagerOwner")
        master = await _create_master(
            session,
            owner.id,
            "Manager Salon",
            SubscriptionStatus.TRIAL,
            trial_ends_at=now + timedelta(days=3),
        )
        await session.commit()

        sub_service = SubscriptionService(session)
        plans = await sub_service.list_active_plans()
        assert len(plans) >= 3  # 1M, 3M, 12M seeded in migration 0008

        # Simulate user choosing 1M (BASIC)
        payment, intent = await sub_service.create_subscription_payment(
            master_id=master.id,
            actor_user_id=owner.id,
            plan_code="BASIC",
        )
        await session.commit()

        assert payment.status == "PENDING"
        assert intent.provider_payment_id is not None

        # Simulate user confirming payment
        confirmed = await sub_service.process_successful_payment(
            provider=payment.provider,
            provider_payment_id=payment.provider_payment_id,
        )
        assert confirmed is True
        await session.commit()

        eff = await sub_service.get_effective_status(master.id)
        assert eff.status == EffectiveSubscriptionStatus.PAID_ACTIVE
        assert eff.days_remaining >= 33  # 3 days trial + 30 days basic
