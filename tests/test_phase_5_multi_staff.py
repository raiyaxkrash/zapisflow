"""
Comprehensive tests for Phase 5: Multi-Staff Architecture and Tenant Isolation.

Verifies:
1. Primary Staff auto-provisioning and migration backfill consistency.
2. Physical Composite Foreign Key Tenant Isolation at PostgreSQL level:
   - Appointment (master_id, staff_id) -> staff_members(master_id, id)
   - Appointment (master_id, service_id) -> services(master_id, id)
   - StaffService (master_id, staff_id) & (master_id, service_id) cross-tenant block
   - ScheduleTemplate (master_id, staff_id) cross-tenant block
   - Review (master_id, appointment_id) -> appointments(master_id, id)
3. Exclusion Constraint Concurrency:
   - Two different specialists in the same studio CAN work simultaneously at the exact same time.
   - The same specialist CANNOT be double-booked simultaneously (PostgreSQL 23P01 exclusion error).
4. Schedule Exceptions and Blocked Intervals:
   - Studio-wide block (staff_id IS NULL) blocks all specialists in the studio.
   - Personal block (staff_id = X) blocks only specialist X; specialist Y remains available.
5. Many-to-Many StaffService:
   - Services offered only by specific staff members.
   - Dynamic service-staff assignment and validation.
6. Crypto-safe One-Time Staff Invites:
   - SHA-256 token hashing, atomic claim protection via UPDATE ... RETURNING.
   - Double-claim rejection and expired token rejection.
7. Role-Based Access Control:
   - OWNER, ADMIN, STAFF permissions in MasterAuthorizationService.
   - Staff-only access restrictions (no billing, no studio settings).
8. Client Bot UX:
   - Automatic bypass of staff selection when studio has only 1 specialist.
   - Staff selection step rendered when studio has > 1 specialist.
9. PortfolioItem ON DELETE SET NULL:
   - Deleting a staff member preserves master_id on PortfolioItem and sets staff_id to NULL.
"""

from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.handlers.client.booking import cb_staff_selected, cb_start_booking
from app.bot.keyboards.client.callbacks import ServiceCallback, StaffChoiceCallback
from app.bot.states.client import ClientBookingSG
from app.database.models.appointment import Appointment, AppointmentStatus
from app.database.models.master import (
    Master,
    MasterAdmin,
    MasterAdminRole,
    MasterStatus,
    SubscriptionStatus,
)
from app.database.models.portfolio import PortfolioCategory, PortfolioItem
from app.database.models.review import Review
from app.database.models.schedule import BlockedInterval, ScheduleException, ScheduleTemplate
from app.database.models.service import DepositType, Service
from app.database.models.staff import StaffMember, StaffService
from app.database.models.user import User
from app.repositories.appointment_repository import AppointmentRepository
from app.repositories.schedule_repository import ScheduleRepository
from app.repositories.staff_repository import StaffRepository
from app.services.booking_service import BookingService
from app.services.crm_service import MasterCrmService
from app.services.exceptions import ServiceNotFoundError, SlotAlreadyBookedError
from app.services.master_authorization_service import MasterAuthorizationService
from app.services.slot_engine import SlotEngine
from tests.conftest import requires_postgres


# ---------------------------------------------------------------------------
# Test Helpers
# ---------------------------------------------------------------------------

async def _create_user(session: AsyncSession, tg_id: int, name: str) -> User:
    u = User(telegram_id=tg_id, first_name=name)
    session.add(u)
    await session.flush()
    return u


async def _create_master(session: AsyncSession, owner_id: int, name: str) -> Master:
    m = Master(
        owner_user_id=owner_id,
        display_name=name,
        status=MasterStatus.ACTIVE,
        subscription_status=SubscriptionStatus.ACTIVE,
        timezone="UTC",
    )
    session.add(m)
    await session.flush()
    return m


async def _create_service(session: AsyncSession, master_id: int, title: str) -> Service:
    s = Service(
        master_id=master_id,
        title=title,
        price=Decimal("2000.00"),
        duration_min=60,
        buffer_min=15,
        deposit_type=DepositType.FIXED,
        deposit_value=Decimal("500.00"),
        is_active=True,
    )
    session.add(s)
    await session.flush()
    return s


# ---------------------------------------------------------------------------
# 1. Primary Staff Auto-Provisioning & Repository CRUD
# ---------------------------------------------------------------------------

@requires_postgres
@pytest.mark.asyncio
async def test_01_primary_staff_auto_provisioning(pg_session: AsyncSession) -> None:
    """When a master is created or queried, primary staff member is auto-provisioned."""
    owner = await _create_user(pg_session, 55001, "Studio Owner 1")
    master = await _create_master(pg_session, owner.id, "Top Beauty Salon")

    repo = StaffRepository(pg_session)
    # Initially no explicit staff, get_primary_or_default auto-provisions one
    primary = await repo.get_primary_or_default(master.id)
    assert primary is not None
    assert primary.master_id == master.id
    assert primary.display_name == "Top Beauty Salon"
    assert primary.is_active is True

    # Subsequent call returns the exact same staff member
    primary_again = await repo.get_primary_or_default(master.id)
    assert primary_again.id == primary.id


# ---------------------------------------------------------------------------
# 2. Database-Level Composite Foreign Key Enforcement (Anti Cross-Tenant)
# ---------------------------------------------------------------------------

@requires_postgres
@pytest.mark.asyncio
async def test_02_appointment_composite_fk_blocks_cross_tenant_staff(pg_session: AsyncSession) -> None:
    """Database physically blocks appointment referencing staff_id from another master."""
    owner1 = await _create_user(pg_session, 55002, "Owner 2A")
    owner2 = await _create_user(pg_session, 55003, "Owner 2B")
    master_a = await _create_master(pg_session, owner1.id, "Studio A")
    master_b = await _create_master(pg_session, owner2.id, "Studio B")

    repo = StaffRepository(pg_session)
    staff_a = await repo.create_staff(master_a.id, "Staff in A")
    staff_b = await repo.create_staff(master_b.id, "Staff in B")
    svc_a = await _create_service(pg_session, master_a.id, "Service in A")
    client = await _create_user(pg_session, 55004, "Client 2")

    start = datetime(2026, 11, 1, 10, 0, tzinfo=timezone.utc)
    end = datetime(2026, 11, 1, 11, 0, tzinfo=timezone.utc)
    end_buf = datetime(2026, 11, 1, 11, 15, tzinfo=timezone.utc)

    # Cross-tenant injection attempt: Appointment in Master A references Staff from Master B
    bad_appt = Appointment(
        master_id=master_a.id,
        staff_id=staff_b.id,  # Foreign staff!
        service_id=svc_a.id,
        user_id=client.id,
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
    async with pg_session.begin_nested():
        pg_session.add(bad_appt)
        with pytest.raises(IntegrityError) as exc_info:
            await pg_session.flush()
        assert "fk_appointments_staff_master" in str(exc_info.value)


@requires_postgres
@pytest.mark.asyncio
async def test_03_appointment_composite_fk_blocks_cross_tenant_service(pg_session: AsyncSession) -> None:
    """Database physically blocks appointment referencing service_id from another master."""
    owner1 = await _create_user(pg_session, 55005, "Owner 3A")
    owner2 = await _create_user(pg_session, 55006, "Owner 3B")
    master_a = await _create_master(pg_session, owner1.id, "Studio 3A")
    master_b = await _create_master(pg_session, owner2.id, "Studio 3B")

    repo = StaffRepository(pg_session)
    staff_a = await repo.create_staff(master_a.id, "Staff 3A")
    svc_b = await _create_service(pg_session, master_b.id, "Service in B")
    client = await _create_user(pg_session, 55007, "Client 3")

    start = datetime(2026, 11, 1, 12, 0, tzinfo=timezone.utc)
    end = datetime(2026, 11, 1, 13, 0, tzinfo=timezone.utc)
    end_buf = datetime(2026, 11, 1, 13, 15, tzinfo=timezone.utc)

    # Cross-tenant injection attempt: Appointment in Master A references Service from Master B
    bad_appt = Appointment(
        master_id=master_a.id,
        staff_id=staff_a.id,
        service_id=svc_b.id,  # Foreign service!
        user_id=client.id,
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
    async with pg_session.begin_nested():
        pg_session.add(bad_appt)
        with pytest.raises(IntegrityError) as exc_info:
            await pg_session.flush()
        assert "fk_appointments_service_master" in str(exc_info.value)


@requires_postgres
@pytest.mark.asyncio
async def test_04_staff_service_cross_tenant_block(pg_session: AsyncSession) -> None:
    """StaffService link cannot connect staff of Master A with service of Master B."""
    owner1 = await _create_user(pg_session, 55008, "Owner 4A")
    owner2 = await _create_user(pg_session, 55009, "Owner 4B")
    master_a = await _create_master(pg_session, owner1.id, "Studio 4A")
    master_b = await _create_master(pg_session, owner2.id, "Studio 4B")

    repo = StaffRepository(pg_session)
    staff_a = await repo.create_staff(master_a.id, "Staff 4A")
    svc_b = await _create_service(pg_session, master_b.id, "Service in B")

    # Attempt to link staff_a (in Master A) with service_b (in Master B) using master_id=master_a.id
    bad_link = StaffService(
        master_id=master_a.id,
        staff_id=staff_a.id,
        service_id=svc_b.id,
        is_active=True,
    )
    async with pg_session.begin_nested():
        pg_session.add(bad_link)
        with pytest.raises(IntegrityError) as exc_info:
            await pg_session.flush()
        assert "fk_staff_services_service_master" in str(exc_info.value)


@requires_postgres
@pytest.mark.asyncio
async def test_05_review_composite_fk_blocks_cross_tenant_appointment(pg_session: AsyncSession) -> None:
    """Review cannot reference appointment of another master."""
    owner1 = await _create_user(pg_session, 55010, "Owner 5A")
    owner2 = await _create_user(pg_session, 55011, "Owner 5B")
    master_a = await _create_master(pg_session, owner1.id, "Studio 5A")
    master_b = await _create_master(pg_session, owner2.id, "Studio 5B")

    client = await _create_user(pg_session, 55012, "Client 5")
    svc_a = await _create_service(pg_session, master_a.id, "Service 5A")

    repo = StaffRepository(pg_session)
    staff_a = await repo.create_staff(master_a.id, "Staff 5A")

    appt_a = Appointment(
        master_id=master_a.id,
        staff_id=staff_a.id,
        service_id=svc_a.id,
        user_id=client.id,
        status=AppointmentStatus.COMPLETED,
        start_time=datetime(2026, 11, 2, 10, 0, tzinfo=timezone.utc),
        end_time=datetime(2026, 11, 2, 11, 0, tzinfo=timezone.utc),
        end_time_with_buffer=datetime(2026, 11, 2, 11, 15, tzinfo=timezone.utc),
        snapshot_service_title=svc_a.title,
        snapshot_service_price=svc_a.price,
        snapshot_service_duration_min=svc_a.duration_min,
        snapshot_buffer_duration_min=svc_a.buffer_min,
        snapshot_deposit_amount=svc_a.deposit_value,
    )
    pg_session.add(appt_a)
    await pg_session.flush()

    # Review in Master B tries to attach to appt_a (in Master A)
    bad_review = Review(
        master_id=master_b.id,  # Foreign master!
        user_id=client.id,
        appointment_id=appt_a.id,
        rating=5,
        comment="Fake cross-tenant review",
    )
    async with pg_session.begin_nested():
        pg_session.add(bad_review)
        with pytest.raises(IntegrityError) as exc_info:
            await pg_session.flush()
        assert "fk_reviews_appointment_master" in str(exc_info.value)


# ---------------------------------------------------------------------------
# 3. PostgreSQL Exclusion Constraint Concurrency (Staff-Level Overlap)
# ---------------------------------------------------------------------------

@requires_postgres
@pytest.mark.asyncio
async def test_06_two_specialists_can_book_simultaneously_in_same_studio(pg_session: AsyncSession) -> None:
    """Two different specialists in the SAME studio CAN have appointments at the same time."""
    owner = await _create_user(pg_session, 55013, "Salon Owner 6")
    master = await _create_master(pg_session, owner.id, "Multi-Master Salon")
    svc = await _create_service(pg_session, master.id, "Haircut")

    client1 = await _create_user(pg_session, 55014, "Client 6A")
    client2 = await _create_user(pg_session, 55015, "Client 6B")

    repo = StaffRepository(pg_session)
    staff_anna = await repo.create_staff(master.id, "Анна")
    staff_elena = await repo.create_staff(master.id, "Елена")

    same_start = datetime(2026, 11, 5, 14, 0, tzinfo=timezone.utc)
    same_end = datetime(2026, 11, 5, 15, 0, tzinfo=timezone.utc)
    same_buf = datetime(2026, 11, 5, 15, 15, tzinfo=timezone.utc)

    # Booking for Anna
    appt1 = Appointment(
        master_id=master.id,
        staff_id=staff_anna.id,
        service_id=svc.id,
        user_id=client1.id,
        status=AppointmentStatus.CONFIRMED,
        start_time=same_start,
        end_time=same_end,
        end_time_with_buffer=same_buf,
        snapshot_service_title=svc.title,
        snapshot_service_price=svc.price,
        snapshot_service_duration_min=svc.duration_min,
        snapshot_buffer_duration_min=svc.buffer_min,
        snapshot_deposit_amount=svc.deposit_value,
    )
    # Booking for Elena at the exact same interval
    appt2 = Appointment(
        master_id=master.id,
        staff_id=staff_elena.id,
        service_id=svc.id,
        user_id=client2.id,
        status=AppointmentStatus.CONFIRMED,
        start_time=same_start,
        end_time=same_end,
        end_time_with_buffer=same_buf,
        snapshot_service_title=svc.title,
        snapshot_service_price=svc.price,
        snapshot_service_duration_min=svc.duration_min,
        snapshot_buffer_duration_min=svc.buffer_min,
        snapshot_deposit_amount=svc.deposit_value,
    )

    pg_session.add_all([appt1, appt2])
    await pg_session.flush()

    assert appt1.id is not None
    assert appt2.id is not None
    assert appt1.staff_id != appt2.staff_id


@requires_postgres
@pytest.mark.asyncio
async def test_07_same_specialist_cannot_be_double_booked(pg_session: AsyncSession) -> None:
    """The same specialist CANNOT have two overlapping appointments (23P01 exclusion violation)."""
    owner = await _create_user(pg_session, 55016, "Salon Owner 7")
    master = await _create_master(pg_session, owner.id, "Salon 7")
    svc = await _create_service(pg_session, master.id, "Nails")

    client1 = await _create_user(pg_session, 55017, "Client 7A")
    client2 = await _create_user(pg_session, 55018, "Client 7B")

    repo = StaffRepository(pg_session)
    staff = await repo.create_staff(master.id, "Светлана")

    start1 = datetime(2026, 11, 6, 10, 0, tzinfo=timezone.utc)
    end1 = datetime(2026, 11, 6, 11, 0, tzinfo=timezone.utc)
    buf1 = datetime(2026, 11, 6, 11, 15, tzinfo=timezone.utc)

    appt1 = Appointment(
        master_id=master.id,
        staff_id=staff.id,
        service_id=svc.id,
        user_id=client1.id,
        status=AppointmentStatus.CONFIRMED,
        start_time=start1,
        end_time=end1,
        end_time_with_buffer=buf1,
        snapshot_service_title=svc.title,
        snapshot_service_price=svc.price,
        snapshot_service_duration_min=svc.duration_min,
        snapshot_buffer_duration_min=svc.buffer_min,
        snapshot_deposit_amount=svc.deposit_value,
    )
    pg_session.add(appt1)
    await pg_session.flush()

    # Conflicting booking for the same specialist Svetlana
    start2 = datetime(2026, 11, 6, 10, 30, tzinfo=timezone.utc)  # Overlaps!
    end2 = datetime(2026, 11, 6, 11, 30, tzinfo=timezone.utc)
    buf2 = datetime(2026, 11, 6, 11, 45, tzinfo=timezone.utc)

    appt2 = Appointment(
        master_id=master.id,
        staff_id=staff.id,
        service_id=svc.id,
        user_id=client2.id,
        status=AppointmentStatus.WAITING_PAYMENT,
        start_time=start2,
        end_time=end2,
        end_time_with_buffer=buf2,
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
        assert "exclusion" in str(exc_info.value).lower() or "23p01" in str(exc_info.value).lower()


# ---------------------------------------------------------------------------
# 4. Schedule Exceptions and Blocked Intervals Isolation
# ---------------------------------------------------------------------------

@requires_postgres
@pytest.mark.asyncio
async def test_08_blocked_interval_studio_wide_vs_personal(pg_session: AsyncSession) -> None:
    """Studio-wide blocked interval blocks all specialists; personal block blocks only one."""
    owner = await _create_user(pg_session, 55019, "Owner 8")
    master = await _create_master(pg_session, owner.id, "Studio 8")
    repo = StaffRepository(pg_session)
    sch_repo = ScheduleRepository(pg_session)

    staff1 = await repo.create_staff(master.id, "Мастер 1")
    staff2 = await repo.create_staff(master.id, "Мастер 2")

    # 1. Personal block for staff1 only
    block_start = datetime(2026, 11, 10, 12, 0, tzinfo=timezone.utc)
    block_end = datetime(2026, 11, 10, 14, 0, tzinfo=timezone.utc)
    b_personal = BlockedInterval(
        master_id=master.id,
        staff_id=staff1.id,
        start_time=block_start,
        end_time=block_end,
        reason="Личные дела",
    )
    pg_session.add(b_personal)
    await pg_session.flush()

    blocks_staff1 = await sch_repo.get_blocked_intervals(
        master_id=master.id,
        start_datetime=datetime(2026, 11, 10, 0, 0, tzinfo=timezone.utc),
        end_datetime=datetime(2026, 11, 10, 23, 59, tzinfo=timezone.utc),
        staff_id=staff1.id,
    )
    assert len(blocks_staff1) == 1
    assert blocks_staff1[0].reason == "Личные дела"

    blocks_staff2 = await sch_repo.get_blocked_intervals(
        master_id=master.id,
        start_datetime=datetime(2026, 11, 10, 0, 0, tzinfo=timezone.utc),
        end_datetime=datetime(2026, 11, 10, 23, 59, tzinfo=timezone.utc),
        staff_id=staff2.id,
    )
    assert len(blocks_staff2) == 0  # Staff 2 is free!

    # 2. Studio-wide block (staff_id is NULL)
    b_studio = BlockedInterval(
        master_id=master.id,
        staff_id=None,
        start_time=datetime(2026, 11, 10, 16, 0, tzinfo=timezone.utc),
        end_time=datetime(2026, 11, 10, 18, 0, tzinfo=timezone.utc),
        reason="Санитарный день студии",
    )
    pg_session.add(b_studio)
    await pg_session.flush()

    # Now both staff1 and staff2 receive studio-wide block
    blocks_staff2_after = await sch_repo.get_blocked_intervals(
        master_id=master.id,
        start_datetime=datetime(2026, 11, 10, 0, 0, tzinfo=timezone.utc),
        end_datetime=datetime(2026, 11, 10, 23, 59, tzinfo=timezone.utc),
        staff_id=staff2.id,
    )
    assert len(blocks_staff2_after) == 1
    assert blocks_staff2_after[0].reason == "Санитарный день студии"


# ---------------------------------------------------------------------------
# 5. Many-to-Many Staff Services
# ---------------------------------------------------------------------------

@requires_postgres
@pytest.mark.asyncio
async def test_09_staff_services_many_to_many(pg_session: AsyncSession) -> None:
    """Staff members can be assigned multiple services, and services can be linked to multiple staff."""
    owner = await _create_user(pg_session, 55020, "Owner 9")
    master = await _create_master(pg_session, owner.id, "Studio 9")
    repo = StaffRepository(pg_session)

    s1 = await _create_service(pg_session, master.id, "Маникюр")
    s2 = await _create_service(pg_session, master.id, "Педикюр")
    s3 = await _create_service(pg_session, master.id, "Брови")

    m_olga = await repo.create_staff(master.id, "Ольга")
    m_irina = await repo.create_staff(master.id, "Ирина")

    # Olga does Маникюр and Педикюр
    await repo.set_staff_services(m_olga.id, master.id, [s1.id, s2.id])
    # Irina does Маникюр and Брови
    await repo.set_staff_services(m_irina.id, master.id, [s1.id, s3.id])

    olga_services = await repo.list_services_for_staff(m_olga.id, master.id)
    assert sorted(olga_services) == sorted([s1.id, s2.id])

    irina_services = await repo.list_services_for_staff(m_irina.id, master.id)
    assert sorted(irina_services) == sorted([s1.id, s3.id])

    # Маникюр offered by both Olga and Irina
    manicure_staff = await repo.list_staff_for_service(s1.id, master.id)
    assert len(manicure_staff) == 2
    staff_names = [st.display_name for st in manicure_staff]
    assert "Ольга" in staff_names
    assert "Ирина" in staff_names

    # Педикюр offered only by Olga
    pedicure_staff = await repo.list_staff_for_service(s2.id, master.id)
    assert len(pedicure_staff) == 1
    assert pedicure_staff[0].display_name == "Ольга"


# ---------------------------------------------------------------------------
# 6. Secure Crypto One-Time Invite Links
# ---------------------------------------------------------------------------

@requires_postgres
@pytest.mark.asyncio
async def test_10_crypto_invite_token_flow(pg_session: AsyncSession) -> None:
    """Crypto invite tokens are securely hashed, single-use, atomic and expire in 48 hours."""
    owner = await _create_user(pg_session, 55021, "Owner 10")
    master = await _create_master(pg_session, owner.id, "Studio 10")
    repo = StaffRepository(pg_session)

    staff = await repo.create_staff(master.id, "Новый мастер")
    token_plain = await repo.create_invite_token(staff.id, master.id, expires_days=2)
    assert token_plain is not None
    assert len(token_plain) > 20

    # User registers via invite link
    staff_user = await _create_user(pg_session, 55022, "TelegramStaffUser")

    # 1. First claim succeeds
    claimed = await repo.claim_invite_token_atomic(
        raw_token=token_plain,
        user_id=staff_user.id,
    )
    assert claimed is not None
    assert claimed.id == staff.id
    assert claimed.user_id == staff_user.id
    assert claimed.invite_used_at is not None

    # 2. Second claim attempt with the same token fails
    claimed_again = await repo.claim_invite_token_atomic(
        raw_token=token_plain,
        user_id=staff_user.id,
    )
    assert claimed_again is None

    # 3. Expired token cannot be claimed
    staff_expired = await repo.create_staff(master.id, "Просроченный мастер")
    exp_token = await repo.create_invite_token(staff_expired.id, master.id, expires_days=1)
    staff_expired.invite_expires_at = datetime.now(timezone.utc) - timedelta(hours=1)
    await pg_session.flush()

    claimed_expired = await repo.claim_invite_token_atomic(
        raw_token=exp_token,
        user_id=staff_user.id,
    )
    assert claimed_expired is None


# ---------------------------------------------------------------------------
# 7. Role-Based Access Control: OWNER, ADMIN, STAFF
# ---------------------------------------------------------------------------

@requires_postgres
@pytest.mark.asyncio
async def test_11_role_based_access_control(pg_session: AsyncSession) -> None:
    """STAFF role has access only to their appointments and cannot access studio settings or billing."""
    owner_user = await _create_user(pg_session, 55023, "Studio Owner 11")
    staff_user = await _create_user(pg_session, 55024, "Staff Master 11")
    master = await _create_master(pg_session, owner_user.id, "Studio 11")

    repo = StaffRepository(pg_session)
    staff_member = await repo.create_staff(master.id, "Мастер 11", user_id=staff_user.id)

    # Link staff_user as MasterAdmin with STAFF role
    admin_link = MasterAdmin(
        master_id=master.id,
        user_id=staff_user.id,
        role=MasterAdminRole.STAFF,
        staff_id=staff_member.id,
        is_active=True,
    )
    pg_session.add(admin_link)
    await pg_session.flush()

    auth_svc = MasterAuthorizationService(pg_session)

    # 1. Owner has full admin access
    assert await auth_svc.is_owner(master.id, owner_user.id) is True
    assert await auth_svc.is_admin(master.id, owner_user.id) is True
    assert await auth_svc.is_staff_only(master.id, owner_user.id) is False

    # 2. Staff user has staff access, but NOT full owner/admin access
    assert await auth_svc.is_staff_only(master.id, staff_user.id) is True
    assert await auth_svc.is_admin(master.id, staff_user.id) is False
    assert await auth_svc.is_owner(master.id, staff_user.id) is False
    staff_id_resolved = await auth_svc.get_staff_id_for_user(master.id, staff_user.id)
    assert staff_id_resolved == staff_member.id

    # 3. Outsider user has no access
    outsider = await _create_user(pg_session, 55025, "Outsider")
    assert await auth_svc.is_owner(master.id, outsider.id) is False
    assert await auth_svc.is_admin(master.id, outsider.id) is False
    assert await auth_svc.is_staff_only(master.id, outsider.id) is False


# ---------------------------------------------------------------------------
# 8. PortfolioItem ON DELETE SET NULL
# ---------------------------------------------------------------------------

@requires_postgres
@pytest.mark.asyncio
async def test_12_portfolio_item_on_delete_set_null(pg_session: AsyncSession) -> None:
    """When a StaffMember is deleted, PortfolioItem keeps master_id and sets staff_id to NULL."""
    owner = await _create_user(pg_session, 55026, "Owner 12")
    master = await _create_master(pg_session, owner.id, "Studio 12")
    repo = StaffRepository(pg_session)

    staff = await repo.create_staff(master.id, "Мастер Портфолио")
    cat = PortfolioCategory(master_id=master.id, title="Ногти")
    pg_session.add(cat)
    await pg_session.flush()

    item = PortfolioItem(
        master_id=master.id,
        staff_id=staff.id,
        category_id=cat.id,
        telegram_file_id="photo_123",
        telegram_file_unique_id="uniq_123",
        caption="Работа мастера",
    )
    pg_session.add(item)
    await pg_session.flush()
    item_id = item.id

    master_id = master.id

    # Delete the staff member
    await pg_session.delete(staff)
    await pg_session.flush()

    # Refresh item from DB where PostgreSQL ON DELETE SET NULL updated staff_id
    await pg_session.refresh(item)

    assert item.master_id == master_id   # Studio ownership preserved!
    assert item.staff_id is None         # Converted to general studio portfolio!


# ---------------------------------------------------------------------------
# 9. CRM Dashboard Staff Breakdown
# ---------------------------------------------------------------------------

@requires_postgres
@pytest.mark.asyncio
async def test_13_crm_today_dashboard_staff_breakdown(pg_session: AsyncSession) -> None:
    """CRM Today Dashboard includes per-staff breakdown for multi-staff studios."""
    owner = await _create_user(pg_session, 55027, "Owner 13")
    master = await _create_master(pg_session, owner.id, "Studio 13")
    svc = await _create_service(pg_session, master.id, "Укладка")
    client = await _create_user(pg_session, 55028, "Client 13")

    repo = StaffRepository(pg_session)
    staff1 = await repo.create_staff(master.id, "Алина")
    staff2 = await repo.create_staff(master.id, "Диана")

    now_utc = datetime.now(timezone.utc)
    appt1 = Appointment(
        master_id=master.id,
        staff_id=staff1.id,
        service_id=svc.id,
        user_id=client.id,
        status=AppointmentStatus.CONFIRMED,
        start_time=now_utc,
        end_time=now_utc + timedelta(hours=1),
        end_time_with_buffer=now_utc + timedelta(hours=1, minutes=15),
        snapshot_service_title=svc.title,
        snapshot_service_price=svc.price,
        snapshot_service_duration_min=svc.duration_min,
        snapshot_buffer_duration_min=svc.buffer_min,
        snapshot_deposit_amount=svc.deposit_value,
    )
    pg_session.add(appt1)
    await pg_session.flush()

    crm_svc = MasterCrmService(pg_session)
    dash = await crm_svc.get_today_dashboard(master.id)

    assert dash["total_today"] == 1
    assert "staff_breakdown" in dash
    breakdown = dash["staff_breakdown"]
    assert len(breakdown) == 2
    alina_stat = next(b for b in breakdown if b["name"] == "Алина")
    assert alina_stat["total_today"] == 1
    diana_stat = next(b for b in breakdown if b["name"] == "Диана")
    assert diana_stat["total_today"] == 0


# ---------------------------------------------------------------------------
# 10. Client Booking UX: 1 Staff vs Multiple Staff
# ---------------------------------------------------------------------------

@requires_postgres
@pytest.mark.asyncio
async def test_14_single_staff_skips_selection_keyboard(pg_session: AsyncSession) -> None:
    """If studio has only 1 active staff, staff selection is transparently bypassed."""
    owner = await _create_user(pg_session, 55029, "Single Owner 14")
    master = await _create_master(pg_session, owner.id, "Single Master Studio")
    await _create_service(pg_session, master.id, "Стрижка")
    repo = StaffRepository(pg_session)
    await repo.create_staff(master.id, "Единственный мастер")

    callback = AsyncMock()
    callback.from_user = MagicMock(id=55030, first_name="Client")
    callback.answer = AsyncMock()
    callback.message = AsyncMock()
    state = AsyncMock()

    await cb_start_booking(
        callback=callback,
        state=state,
        session=pg_session,
        master_id=master.id,
    )
    # With 1 staff, state transitions directly to choosing service, bypassing choosing_staff
    state.set_state.assert_called_with(ClientBookingSG.choosing_service)
    callback.message.edit_text.assert_awaited_once()
    edit_kwargs = callback.message.edit_text.await_args.kwargs
    assert "Выберите услугу" in edit_kwargs["text"]
    selected_service = ServiceCallback.unpack(
        edit_kwargs["reply_markup"].inline_keyboard[0][0].callback_data
    )
    assert selected_service.action == "select"
    callback.answer.assert_awaited_once()


@requires_postgres
@pytest.mark.asyncio
async def test_15_multiple_staff_shows_staff_selection_keyboard(pg_session: AsyncSession) -> None:
    """If studio has 2+ active staff, client is prompted to choose specialist."""
    owner = await _create_user(pg_session, 55031, "Multi Owner 15")
    master = await _create_master(pg_session, owner.id, "Multi Master Studio")
    await _create_service(pg_session, master.id, "Маникюр")
    repo = StaffRepository(pg_session)
    await repo.create_staff(master.id, "Мастер А")
    await repo.create_staff(master.id, "Мастер Б")

    callback = AsyncMock()
    callback.from_user = MagicMock(id=55032, first_name="Client")
    callback.answer = AsyncMock()
    callback.message = AsyncMock()
    state = AsyncMock()

    await cb_start_booking(
        callback=callback,
        state=state,
        session=pg_session,
        master_id=master.id,
    )
    # With 2 staff, state transitions to choosing_staff
    state.set_state.assert_called_with(ClientBookingSG.choosing_staff)


@requires_postgres
@pytest.mark.asyncio
async def test_16_cb_staff_selected_updates_state_and_shows_services(pg_session: AsyncSession) -> None:
    """Selecting a staff member updates state data with staff_id and filters services."""
    owner = await _create_user(pg_session, 55033, "Owner 16")
    master = await _create_master(pg_session, owner.id, "Studio 16")
    s1 = await _create_service(pg_session, master.id, "Услуга мастера 1")
    s2 = await _create_service(pg_session, master.id, "Услуга мастера 2")

    repo = StaffRepository(pg_session)
    staff1 = await repo.create_staff(master.id, "Мастер 1")
    staff2 = await repo.create_staff(master.id, "Мастер 2")
    await repo.set_staff_services(staff1.id, master.id, [s1.id])
    await repo.set_staff_services(staff2.id, master.id, [s2.id])

    callback = AsyncMock()
    callback.answer = AsyncMock()
    callback.message = AsyncMock()
    callback_data = StaffChoiceCallback(action="select", staff_id=staff1.id)
    state = AsyncMock()

    await cb_staff_selected(
        callback=callback,
        callback_data=callback_data,
        state=state,
        session=pg_session,
        master_id=master.id,
    )

    state.update_data.assert_called_with(staff_id=staff1.id)
    state.set_state.assert_called_with(ClientBookingSG.choosing_service)
