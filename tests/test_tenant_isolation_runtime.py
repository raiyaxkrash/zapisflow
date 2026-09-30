"""Comprehensive runtime tests for Strict Tenant Isolation (Phase 3).

Verifies all 22 required multi-tenant isolation scenarios on live PostgreSQL:
1. Service A not readable via master B.
2. Service A not editable by B (toggle/archive).
3. Appointment A not readable by B.
4. Appointment A not cancellable by B (throws AppointmentNotFoundError).
5. Appointment A not reschedulable by B (throws AppointmentNotFoundError).
6. Payment A not readable by B.
7. Payment A not approved by B (throws PaymentNotFoundError).
8. Payment A not rejected by B (throws PaymentNotFoundError).
9. PaymentProof A not accessible to B.
10. Schedule A does not affect B.
11. Blocked interval A does not affect slots B.
12. Portfolio A not readable by B.
13. Broadcast A picks only clients of master A.
14. Marketing TRUE at A and FALSE at B handled independently.
15. CRM A contains no clients unique to B.
16. Client X at both A/B has separate notes.
17. Analytics A does not include revenue B.
18. "My appointments" for Client X in bot-context A excludes B.
19. Callback with foreign appointment_id gets NotFound (IDOR check).
20. Callback with foreign payment_id gets NotFound (IDOR check).
21. Service B cannot be used to book at Master A (ServiceNotFoundError).
22. Same Telegram User can be owner of A and client of B without collision.
"""

from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models.appointment import Appointment, AppointmentStatus
from app.database.models.master import (
    Master,
    MasterClient,
    MasterSettings,
    MasterStatus,
    SubscriptionStatus,
)
from app.database.models.payment import MediaType, Payment, PaymentProof, PaymentStatus
from app.database.models.portfolio import PortfolioCategory, PortfolioItem
from app.database.models.schedule import BlockedInterval, ScheduleTemplate
from app.database.models.service import DepositType, Service
from app.database.models.user import Admin, User
from app.repositories.appointment_repository import AppointmentRepository
from app.repositories.master_client_repository import MasterClientRepository
from app.repositories.master_settings_repository import MasterSettingsRepository
from app.repositories.payment_repository import PaymentRepository
from app.repositories.portfolio_repository import PortfolioRepository
from app.repositories.schedule_repository import ScheduleRepository
from app.repositories.service_repository import ServiceRepository
from app.repositories.user_repository import UserRepository
from app.services.analytics_service import AnalyticsService
from app.services.booking_service import BookingService
from app.services.broadcast_service import BroadcastService
from app.services.exceptions import (
    BookingNotFoundError,
    PaymentNotFoundError,
    ServiceNotFoundError,
)
from app.services.payment_service import PaymentService
from app.services.slot_engine import SlotEngine
from tests.conftest import requires_postgres


# ---------------------------------------------------------------------------
# Fixture Helpers
# ---------------------------------------------------------------------------

async def _create_user(session: AsyncSession, telegram_id: int, first_name: str = "User") -> User:
    user = User(telegram_id=telegram_id, first_name=first_name)
    session.add(user)
    await session.flush()
    return user


async def _create_admin(session: AsyncSession, user_id: int) -> Admin:
    admin = Admin(user_id=user_id, role="MASTER", is_active=True)
    session.add(admin)
    await session.flush()
    return admin


async def _create_master(
    session: AsyncSession, owner_user_id: int, display_name: str, timezone_name: str = "Europe/Moscow"
) -> Master:
    master = Master(
        owner_user_id=owner_user_id,
        display_name=display_name,
        status=MasterStatus.ACTIVE,
        subscription_status=SubscriptionStatus.ACTIVE,
        timezone=timezone_name,
    )
    session.add(master)
    await session.flush()
    settings = MasterSettings(master_id=master.id, studio_address="Test Address", hold_duration_minutes=30)
    session.add(settings)
    await session.flush()
    return master


async def _create_service(session: AsyncSession, master_id: int, title: str = "Nails", price: Decimal = Decimal("2000.00")) -> Service:
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
) -> Appointment:
    app = Appointment(
        master_id=master_id,
        user_id=user_id,
        service_id=service.id,
        status=status,
        start_time=start_time,
        end_time=start_time + timedelta(minutes=service.duration_min),
        end_time_with_buffer=start_time + timedelta(minutes=service.duration_min + service.buffer_min),
        hold_until=datetime.now(timezone.utc) + timedelta(minutes=30),
        snapshot_service_title=service.title,
        snapshot_service_price=service.price,
        snapshot_service_duration_min=service.duration_min,
        snapshot_buffer_duration_min=service.buffer_min,
        snapshot_deposit_amount=service.deposit_value,
    )
    session.add(app)
    await session.flush()
    return app


async def _create_payment(
    session: AsyncSession,
    master_id: int,
    appointment_id: int,
    user_id: int,
    amount: Decimal = Decimal("500.00"),
    status: PaymentStatus = PaymentStatus.PENDING,
) -> Payment:
    payment = Payment(
        master_id=master_id,
        appointment_id=appointment_id,
        user_id=user_id,
        amount=amount,
        status=status,
    )
    session.add(payment)
    await session.flush()
    return payment


# ---------------------------------------------------------------------------
# Runtime Isolation Tests (Scenarios 1 - 22)
# ---------------------------------------------------------------------------

@requires_postgres
@pytest.mark.asyncio
async def test_01_service_a_not_readable_via_master_b(pg_session: AsyncSession) -> None:
    owner = await _create_user(pg_session, 80001, "Owner1")
    master_a = await _create_master(pg_session, owner.id, "Master A")
    master_b = await _create_master(pg_session, owner.id, "Master B")
    svc_a = await _create_service(pg_session, master_a.id, "Service A")

    repo = ServiceRepository(pg_session)
    assert await repo.get_by_id(svc_a.id, master_id=master_b.id) is None
    b_active = await repo.list_active(master_id=master_b.id)
    assert svc_a.id not in [s.id for s in b_active]
    b_all = await repo.list_all_for_admin(master_id=master_b.id)
    assert svc_a.id not in [s.id for s in b_all]


@requires_postgres
@pytest.mark.asyncio
async def test_02_service_a_not_editable_by_b(pg_session: AsyncSession) -> None:
    owner = await _create_user(pg_session, 80002, "Owner2")
    master_a = await _create_master(pg_session, owner.id, "Master A")
    master_b = await _create_master(pg_session, owner.id, "Master B")
    svc_a = await _create_service(pg_session, master_a.id, "Service A")

    repo = ServiceRepository(pg_session)
    assert await repo.toggle_active(svc_a.id, master_id=master_b.id) is None
    assert await repo.archive(svc_a.id, master_id=master_b.id) is False

    rechecked_a = await repo.get_by_id(svc_a.id, master_id=master_a.id)
    assert rechecked_a.is_active is True
    assert rechecked_a.is_archived is False


@requires_postgres
@pytest.mark.asyncio
async def test_03_appointment_a_not_readable_by_b(pg_session: AsyncSession) -> None:
    owner = await _create_user(pg_session, 80003, "Owner3")
    client = await _create_user(pg_session, 80004, "Client3")
    master_a = await _create_master(pg_session, owner.id, "Master A")
    master_b = await _create_master(pg_session, owner.id, "Master B")
    svc_a = await _create_service(pg_session, master_a.id, "Service A")

    start_dt = datetime.now(timezone.utc) + timedelta(days=2)
    app_a = await _create_appointment(pg_session, master_a.id, client.id, svc_a, start_dt)

    repo = AppointmentRepository(pg_session)
    assert await repo.get_by_id(app_a.id, master_id=master_b.id) is None
    assert await repo.get_by_id_with_relations(app_a.id, master_id=master_b.id) is None
    range_b = await repo.get_active_for_range(
        master_id=master_b.id,
        start_datetime=start_dt - timedelta(days=1),
        end_datetime=start_dt + timedelta(days=1),
    )
    assert app_a.id not in [a.id for a in range_b]


@requires_postgres
@pytest.mark.asyncio
async def test_04_appointment_a_not_cancellable_by_b(pg_session: AsyncSession) -> None:
    owner = await _create_user(pg_session, 80005, "Owner4")
    client = await _create_user(pg_session, 80006, "Client4")
    master_a = await _create_master(pg_session, owner.id, "Master A")
    master_b = await _create_master(pg_session, owner.id, "Master B")
    svc_a = await _create_service(pg_session, master_a.id, "Service A")

    start_dt = datetime.now(timezone.utc) + timedelta(days=2)
    app_a = await _create_appointment(pg_session, master_a.id, client.id, svc_a, start_dt)

    booking_service = BookingService(pg_session)
    with pytest.raises(BookingNotFoundError):
        await booking_service.cancel_booking_by_client(
            appointment_id=app_a.id, user_id=client.id, master_id=master_b.id
        )

    with pytest.raises(BookingNotFoundError):
        await booking_service.cancel_booking_by_admin(
            appointment_id=app_a.id, master_id=master_b.id
        )

    app_check = await AppointmentRepository(pg_session).get_by_id(app_a.id, master_id=master_a.id)
    assert app_check.status == AppointmentStatus.WAITING_PAYMENT


@requires_postgres
@pytest.mark.asyncio
async def test_05_appointment_a_not_reschedulable_by_b(pg_session: AsyncSession) -> None:
    owner = await _create_user(pg_session, 80007, "Owner5")
    admin = await _create_admin(pg_session, owner.id)
    client = await _create_user(pg_session, 80008, "Client5")
    master_a = await _create_master(pg_session, owner.id, "Master A")
    master_b = await _create_master(pg_session, owner.id, "Master B")
    svc_a = await _create_service(pg_session, master_a.id, "Service A")

    start_dt = datetime.now(timezone.utc) + timedelta(days=2)
    app_a = await _create_appointment(pg_session, master_a.id, client.id, svc_a, start_dt)

    booking_service = BookingService(pg_session)
    new_start = start_dt + timedelta(days=1)
    with pytest.raises(BookingNotFoundError):
        await booking_service.reschedule_booking_by_admin(
            appointment_id=app_a.id, new_start_time=new_start, admin_id=admin.id, master_id=master_b.id
        )


@requires_postgres
@pytest.mark.asyncio
async def test_06_payment_a_not_readable_by_b(pg_session: AsyncSession) -> None:
    owner = await _create_user(pg_session, 80009, "Owner6")
    client = await _create_user(pg_session, 80010, "Client6")
    master_a = await _create_master(pg_session, owner.id, "Master A")
    master_b = await _create_master(pg_session, owner.id, "Master B")
    svc_a = await _create_service(pg_session, master_a.id, "Service A")
    app_a = await _create_appointment(pg_session, master_a.id, client.id, svc_a, datetime.now(timezone.utc) + timedelta(days=2))
    pay_a = await _create_payment(pg_session, master_a.id, app_a.id, client.id, status=PaymentStatus.SUBMITTED)

    repo = PaymentRepository(pg_session)
    assert await repo.get_by_id(pay_a.id, master_id=master_b.id) is None
    assert await repo.get_by_id_with_proofs(pay_a.id, master_id=master_b.id) is None

    service = PaymentService(pg_session)
    inbox_b = await service.list_pending_inbox(master_id=master_b.id)
    assert pay_a.id not in [p.id for p in inbox_b]


@requires_postgres
@pytest.mark.asyncio
async def test_07_payment_a_not_approved_by_b(pg_session: AsyncSession) -> None:
    owner = await _create_user(pg_session, 80011, "Owner7")
    admin = await _create_admin(pg_session, owner.id)
    client = await _create_user(pg_session, 80012, "Client7")
    master_a = await _create_master(pg_session, owner.id, "Master A")
    master_b = await _create_master(pg_session, owner.id, "Master B")
    svc_a = await _create_service(pg_session, master_a.id, "Service A")
    app_a = await _create_appointment(pg_session, master_a.id, client.id, svc_a, datetime.now(timezone.utc) + timedelta(days=2))
    pay_a = await _create_payment(pg_session, master_a.id, app_a.id, client.id, status=PaymentStatus.SUBMITTED)

    service = PaymentService(pg_session)
    with pytest.raises(PaymentNotFoundError):
        await service.approve_payment(payment_id=pay_a.id, admin_id=admin.id, master_id=master_b.id)

    check_pay = await PaymentRepository(pg_session).get_by_id(pay_a.id, master_id=master_a.id)
    assert check_pay.status == PaymentStatus.SUBMITTED


@requires_postgres
@pytest.mark.asyncio
async def test_08_payment_a_not_rejected_by_b(pg_session: AsyncSession) -> None:
    owner = await _create_user(pg_session, 80013, "Owner8")
    admin = await _create_admin(pg_session, owner.id)
    client = await _create_user(pg_session, 80014, "Client8")
    master_a = await _create_master(pg_session, owner.id, "Master A")
    master_b = await _create_master(pg_session, owner.id, "Master B")
    svc_a = await _create_service(pg_session, master_a.id, "Service A")
    app_a = await _create_appointment(pg_session, master_a.id, client.id, svc_a, datetime.now(timezone.utc) + timedelta(days=2))
    pay_a = await _create_payment(pg_session, master_a.id, app_a.id, client.id, status=PaymentStatus.SUBMITTED)

    service = PaymentService(pg_session)
    with pytest.raises(PaymentNotFoundError):
        await service.reject_payment(payment_id=pay_a.id, admin_id=admin.id, reason="Bad receipt", master_id=master_b.id)

    check_pay = await PaymentRepository(pg_session).get_by_id(pay_a.id, master_id=master_a.id)
    assert check_pay.status == PaymentStatus.SUBMITTED


@requires_postgres
@pytest.mark.asyncio
async def test_09_payment_proof_a_not_accessible_to_b(pg_session: AsyncSession) -> None:
    owner = await _create_user(pg_session, 80015, "Owner9")
    client = await _create_user(pg_session, 80016, "Client9")
    master_a = await _create_master(pg_session, owner.id, "Master A")
    master_b = await _create_master(pg_session, owner.id, "Master B")
    svc_a = await _create_service(pg_session, master_a.id, "Service A")
    app_a = await _create_appointment(pg_session, master_a.id, client.id, svc_a, datetime.now(timezone.utc) + timedelta(days=2))
    pay_a = await _create_payment(pg_session, master_a.id, app_a.id, client.id, status=PaymentStatus.SUBMITTED)

    proof = PaymentProof(
        payment_id=pay_a.id,
        telegram_file_id="secret_file_123",
        telegram_file_unique_id="unique_123",
        media_type=MediaType.PHOTO,
    )
    pg_session.add(proof)
    await pg_session.flush()

    repo = PaymentRepository(pg_session)
    assert await repo.get_by_id_with_proofs(pay_a.id, master_id=master_b.id) is None


@requires_postgres
@pytest.mark.asyncio
async def test_10_schedule_a_does_not_affect_b(pg_session: AsyncSession) -> None:
    owner = await _create_user(pg_session, 80017, "Owner10")
    master_a = await _create_master(pg_session, owner.id, "Master A")
    master_b = await _create_master(pg_session, owner.id, "Master B")

    tmpl_a = ScheduleTemplate(
        master_id=master_a.id,
        day_of_week=1,  # Tuesday
        work_start=time(9, 0),
        work_end=time(18, 0),
        is_day_off=False,
    )
    pg_session.add(tmpl_a)
    await pg_session.flush()

    repo = ScheduleRepository(pg_session)
    b_tmpls = await repo.get_weekly_templates(master_id=master_b.id)
    assert tmpl_a.id not in [t.id for t in b_tmpls]
    assert await repo.get_template_for_weekday(weekday=1, master_id=master_b.id) is None


@requires_postgres
@pytest.mark.asyncio
async def test_11_blocked_interval_a_does_not_affect_slots_b(pg_session: AsyncSession) -> None:
    owner = await _create_user(pg_session, 80018, "Owner11")
    master_a = await _create_master(pg_session, owner.id, "Master A")
    master_b = await _create_master(pg_session, owner.id, "Master B")

    target_d = date.today() + timedelta(days=5)
    start_dt = datetime(target_d.year, target_d.month, target_d.day, 12, 0, tzinfo=timezone.utc)
    end_dt = datetime(target_d.year, target_d.month, target_d.day, 14, 0, tzinfo=timezone.utc)

    # Master A blocks 12:00 - 14:00
    blocked_a = BlockedInterval(
        master_id=master_a.id,
        start_time=start_dt,
        end_time=end_dt,
        reason="Dentist",
    )
    pg_session.add(blocked_a)

    # Master B sets working day 10:00 - 16:00
    tmpl_b = ScheduleTemplate(
        master_id=master_b.id,
        day_of_week=target_d.weekday(),
        work_start=time(10, 0),
        work_end=time(16, 0),
        is_day_off=False,
    )
    pg_session.add(tmpl_b)
    svc_b = await _create_service(pg_session, master_b.id, "Service B")
    await pg_session.flush()

    # Blocked interval A must NOT be visible to B
    repo = ScheduleRepository(pg_session)
    b_blocked = await repo.get_blocked_intervals(start_dt, end_dt, master_id=master_b.id)
    assert len(b_blocked) == 0

    # Slots for Master B at 12:00 MUST be available despite Master A's block
    engine = SlotEngine(pg_session)
    slots_b = await engine.get_available_slots(service_id=svc_b.id, target_date=target_d, master_id=master_b.id)
    slot_hours = [s.strftime("%H:%M") for s in slots_b]
    assert "12:00" in slot_hours


@requires_postgres
@pytest.mark.asyncio
async def test_12_portfolio_a_not_readable_by_b(pg_session: AsyncSession) -> None:
    owner = await _create_user(pg_session, 80019, "Owner12")
    master_a = await _create_master(pg_session, owner.id, "Master A")
    master_b = await _create_master(pg_session, owner.id, "Master B")

    cat_a = PortfolioCategory(master_id=master_a.id, title="Brows A", display_order=1)
    pg_session.add(cat_a)
    await pg_session.flush()

    item_a = PortfolioItem(
        category_id=cat_a.id,
        telegram_file_id="portfolio_photo_a",
        telegram_file_unique_id="unique_photo_a",
        caption="Great work A",
    )
    pg_session.add(item_a)
    await pg_session.flush()

    repo = PortfolioRepository(pg_session)
    assert await repo.get_category_by_id(cat_a.id, master_id=master_b.id) is None
    assert len(await repo.list_categories(master_id=master_b.id)) == 0
    assert len(await repo.list_items(cat_a.id, master_id=master_b.id)) == 0
    assert await repo.delete_item(item_a.id, master_id=master_b.id) is False
    assert await repo.delete_category(cat_a.id, master_id=master_b.id) is False


@requires_postgres
@pytest.mark.asyncio
async def test_13_broadcast_a_picks_only_clients_of_master_a(pg_session: AsyncSession) -> None:
    owner = await _create_user(pg_session, 80020, "Owner13")
    client1 = await _create_user(pg_session, 80021, "Client1")
    client2 = await _create_user(pg_session, 80022, "Client2")
    master_a = await _create_master(pg_session, owner.id, "Master A")
    master_b = await _create_master(pg_session, owner.id, "Master B")

    # Client 1 registered at Master A with opt-in
    mc_a = MasterClient(master_id=master_a.id, user_id=client1.id, is_marketing_allowed=True, is_bot_blocked=False)
    # Client 2 registered at Master B with opt-in
    mc_b = MasterClient(master_id=master_b.id, user_id=client2.id, is_marketing_allowed=True, is_bot_blocked=False)
    pg_session.add_all([mc_a, mc_b])
    await pg_session.flush()

    service = BroadcastService(pg_session)
    count_a = await service.count_recipients(master_id=master_a.id)
    count_b = await service.count_recipients(master_id=master_b.id)

    assert count_a == 1
    assert count_b == 1


@requires_postgres
@pytest.mark.asyncio
async def test_14_marketing_true_at_a_and_false_at_b_handled_independently(pg_session: AsyncSession) -> None:
    owner = await _create_user(pg_session, 80023, "Owner14")
    shared_client = await _create_user(pg_session, 80024, "SharedClient")
    master_a = await _create_master(pg_session, owner.id, "Master A")
    master_b = await _create_master(pg_session, owner.id, "Master B")

    # User opted IN for Master A, opted OUT for Master B
    mc_a = MasterClient(master_id=master_a.id, user_id=shared_client.id, is_marketing_allowed=True, is_bot_blocked=False)
    mc_b = MasterClient(master_id=master_b.id, user_id=shared_client.id, is_marketing_allowed=False, is_bot_blocked=False)
    pg_session.add_all([mc_a, mc_b])
    await pg_session.flush()

    service = BroadcastService(pg_session)
    assert await service.count_recipients(master_id=master_a.id) == 1
    assert await service.count_recipients(master_id=master_b.id) == 0


@requires_postgres
@pytest.mark.asyncio
async def test_15_crm_a_contains_no_clients_unique_to_b(pg_session: AsyncSession) -> None:
    owner = await _create_user(pg_session, 80025, "Owner15")
    client_b = await _create_user(pg_session, 80026, "UniqueClientB")
    master_a = await _create_master(pg_session, owner.id, "Master A")
    master_b = await _create_master(pg_session, owner.id, "Master B")

    mc_b = MasterClient(master_id=master_b.id, user_id=client_b.id)
    pg_session.add(mc_b)
    await pg_session.flush()

    client_repo = MasterClientRepository(pg_session)
    assert await client_repo.get_client(master_id=master_a.id, user_id=client_b.id) is None
    search_a = await client_repo.search_clients(master_id=master_a.id, search_text="UniqueClientB")
    assert len(search_a) == 0

    user_repo = UserRepository(pg_session)
    crm_stats_a = await user_repo.get_user_crm_stats(user_id=client_b.id, master_id=master_a.id)
    assert crm_stats_a["total_bookings"] == 0


@requires_postgres
@pytest.mark.asyncio
async def test_16_client_x_at_both_a_and_b_has_separate_notes(pg_session: AsyncSession) -> None:
    owner = await _create_user(pg_session, 80027, "Owner16")
    client_x = await _create_user(pg_session, 80028, "ClientX")
    master_a = await _create_master(pg_session, owner.id, "Master A")
    master_b = await _create_master(pg_session, owner.id, "Master B")

    client_repo = MasterClientRepository(pg_session)
    await client_repo.update_notes(master_id=master_a.id, user_id=client_x.id, notes="Prefers pink color")
    await client_repo.update_notes(master_id=master_b.id, user_id=client_x.id, notes="Allergic to latex")

    rec_a = await client_repo.get_client(master_id=master_a.id, user_id=client_x.id)
    rec_b = await client_repo.get_client(master_id=master_b.id, user_id=client_x.id)

    assert rec_a.notes == "Prefers pink color"
    assert rec_b.notes == "Allergic to latex"


@requires_postgres
@pytest.mark.asyncio
async def test_17_analytics_a_does_not_include_revenue_b(pg_session: AsyncSession) -> None:
    owner = await _create_user(pg_session, 80029, "Owner17")
    client = await _create_user(pg_session, 80030, "Client17")
    master_a = await _create_master(pg_session, owner.id, "Master A")
    master_b = await _create_master(pg_session, owner.id, "Master B")

    svc_a = await _create_service(pg_session, master_a.id, "Svc A", price=Decimal("2000.00"))
    svc_b = await _create_service(pg_session, master_b.id, "Svc B", price=Decimal("5000.00"))

    start_dt = datetime.now(timezone.utc)
    app_a = await _create_appointment(pg_session, master_a.id, client.id, svc_a, start_dt, status=AppointmentStatus.COMPLETED)
    app_b = await _create_appointment(pg_session, master_b.id, client.id, svc_b, start_dt, status=AppointmentStatus.COMPLETED)

    analytics = AnalyticsService(pg_session)
    metrics_a = await analytics.get_metrics_for_range(master_id=master_a.id)
    metrics_b = await analytics.get_metrics_for_range(master_id=master_b.id)

    assert metrics_a["revenue"] == Decimal("2000.00")
    assert metrics_a["total"] == 1
    assert metrics_b["revenue"] == Decimal("5000.00")
    assert metrics_b["total"] == 1


@requires_postgres
@pytest.mark.asyncio
async def test_18_my_appointments_client_x_in_context_a_excludes_b(pg_session: AsyncSession) -> None:
    owner = await _create_user(pg_session, 80031, "Owner18")
    client_x = await _create_user(pg_session, 80032, "ClientX18")
    master_a = await _create_master(pg_session, owner.id, "Master A")
    master_b = await _create_master(pg_session, owner.id, "Master B")

    svc_a = await _create_service(pg_session, master_a.id, "Svc A")
    svc_b = await _create_service(pg_session, master_b.id, "Svc B")

    future_dt = datetime.now(timezone.utc) + timedelta(days=3)
    app_a = await _create_appointment(pg_session, master_a.id, client_x.id, svc_a, future_dt, status=AppointmentStatus.CONFIRMED)
    app_b = await _create_appointment(pg_session, master_b.id, client_x.id, svc_b, future_dt, status=AppointmentStatus.CONFIRMED)

    repo = AppointmentRepository(pg_session)
    upcoming_a = await repo.get_user_upcoming(user_id=client_x.id, master_id=master_a.id)
    upcoming_b = await repo.get_user_upcoming(user_id=client_x.id, master_id=master_b.id)

    assert [a.id for a in upcoming_a] == [app_a.id]
    assert [a.id for a in upcoming_b] == [app_b.id]


@requires_postgres
@pytest.mark.asyncio
async def test_19_callback_foreign_appointment_id_handled_safely(pg_session: AsyncSession) -> None:
    """IDOR protection: requesting appointment with wrong master_id returns None."""
    owner = await _create_user(pg_session, 80033, "Owner19")
    client = await _create_user(pg_session, 80034, "Client19")
    master_a = await _create_master(pg_session, owner.id, "Master A")
    master_b = await _create_master(pg_session, owner.id, "Master B")

    svc_b = await _create_service(pg_session, master_b.id, "Svc B")
    app_b = await _create_appointment(pg_session, master_b.id, client.id, svc_b, datetime.now(timezone.utc) + timedelta(days=1))

    repo = AppointmentRepository(pg_session)
    idor_attempt = await repo.get_by_id_with_relations(appointment_id=app_b.id, master_id=master_a.id)
    assert idor_attempt is None


@requires_postgres
@pytest.mark.asyncio
async def test_20_callback_foreign_payment_id_handled_safely(pg_session: AsyncSession) -> None:
    """IDOR protection: requesting payment with wrong master_id returns None."""
    owner = await _create_user(pg_session, 80035, "Owner20")
    client = await _create_user(pg_session, 80036, "Client20")
    master_a = await _create_master(pg_session, owner.id, "Master A")
    master_b = await _create_master(pg_session, owner.id, "Master B")

    svc_b = await _create_service(pg_session, master_b.id, "Svc B")
    app_b = await _create_appointment(pg_session, master_b.id, client.id, svc_b, datetime.now(timezone.utc) + timedelta(days=1))
    pay_b = await _create_payment(pg_session, master_b.id, app_b.id, client.id)

    repo = PaymentRepository(pg_session)
    idor_attempt = await repo.get_by_id_with_proofs(payment_id=pay_b.id, master_id=master_a.id)
    assert idor_attempt is None


@requires_postgres
@pytest.mark.asyncio
async def test_21_service_b_cannot_be_used_to_book_at_master_a(pg_session: AsyncSession) -> None:
    """Prevent cross-tenant service injection when holding booking."""
    owner = await _create_user(pg_session, 80037, "Owner21")
    client = await _create_user(pg_session, 80038, "Client21")
    master_a = await _create_master(pg_session, owner.id, "Master A")
    master_b = await _create_master(pg_session, owner.id, "Master B")

    svc_b = await _create_service(pg_session, master_b.id, "Service of Master B")
    start_time = datetime.now(timezone.utc) + timedelta(days=2)

    booking_service = BookingService(pg_session)
    with pytest.raises(ServiceNotFoundError):
        await booking_service.create_hold_booking(
            user_id=client.id,
            service_id=svc_b.id,
            start_time=start_time,
            master_id=master_a.id,
        )


@requires_postgres
@pytest.mark.asyncio
async def test_22_same_telegram_user_can_be_owner_of_a_and_client_of_b(pg_session: AsyncSession) -> None:
    """Same physical Telegram user is master/owner in tenant A and regular client in tenant B without collisions."""
    dual_user = await _create_user(pg_session, 80039, "DualUser")
    other_owner = await _create_user(pg_session, 80040, "OtherOwner")

    # DualUser owns Master A
    master_a = await _create_master(pg_session, owner_user_id=dual_user.id, display_name="DualUser Salon")
    # OtherOwner owns Master B
    master_b = await _create_master(pg_session, owner_user_id=other_owner.id, display_name="Other Salon")

    # DualUser registers as client of Master B
    client_repo = MasterClientRepository(pg_session)
    mc_b = await client_repo.get_or_create(master_id=master_b.id, user_id=dual_user.id)
    await client_repo.update_notes(master_id=master_b.id, user_id=dual_user.id, notes="Master herself, VIP")

    # Verify DualUser is master owner of A
    assert master_a.owner_user_id == dual_user.id
    # Verify DualUser is client of B with proper isolation
    check_mc_b = await client_repo.get_client(master_id=master_b.id, user_id=dual_user.id)
    assert check_mc_b is not None
    assert check_mc_b.notes == "Master herself, VIP"
    # Verify DualUser is not client of A automatically
    check_mc_a = await client_repo.get_client(master_id=master_a.id, user_id=dual_user.id)
    assert check_mc_a is None
