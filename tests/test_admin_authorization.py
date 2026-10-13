"""Comprehensive integration tests for Dynamic Admin Ownership & Access Control (Phase 4).

Covers all 20 required scenarios on live PostgreSQL:
1. Owner A gets admin access to A.
2. Owner A does not get admin access to B.
3. Admin A gets admin access to A.
4. Admin A does not get admin access to B.
5. Normal client A does not get admin access.
6. User can be Owner A and Client B.
7. User can be Owner A and Admin B.
8. Disabled MasterAdmin loses access immediately.
9. Old callback after revoke fails (access denied).
10. Crafted admin callback by regular user is rejected.
11. Owner dynamically resolves from Master.owner_user_id.
12. ADMIN_IDS in env does not grant access to tenant.
13. Legacy admin of Master 1 correctly recognized.
14. Admin B cannot edit Service A.
15. Admin B cannot approve Payment A.
16. Admin B cannot reschedule Appointment A.
17. Single Telegram ID does not have platform-wide access across masters.
18. require_owner allows owner.
19. require_owner denies regular ADMIN.
20. User with different roles across A/B/C gets correct role per master.
"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.filters.admin import IsAdminFilter
from app.bot.middlewares.user_context import UserContextMiddleware
from app.config.settings import settings
from app.database.models.appointment import Appointment, AppointmentStatus
from app.database.models.master import (
    Master,
    MasterAdmin,
    MasterClient,
    MasterSettings,
    MasterStatus,
    SubscriptionStatus,
)
from app.database.models.payment import MediaType, Payment, PaymentProof, PaymentStatus
from app.database.models.service import DepositType, Service
from app.database.models.user import Admin, User
from app.repositories.master_admin_repository import MasterAdminRepository
from app.repositories.service_repository import ServiceRepository
from app.repositories.user_repository import UserRepository
from app.services.booking_service import BookingService
from app.services.exceptions import (
    AccessDeniedError,
    BookingNotFoundError,
    PaymentNotFoundError,
    ServiceNotFoundError,
)
from app.services.master_authorization_service import AdminRole, MasterAuthorizationService
from app.services.payment_service import PaymentService
from tests.conftest import requires_postgres


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

async def _create_user(session: AsyncSession, telegram_id: int, first_name: str = "User") -> User:
    user = User(telegram_id=telegram_id, first_name=first_name)
    session.add(user)
    await session.flush()
    return user


async def _create_master(
    session: AsyncSession,
    owner_user_id: int,
    display_name: str,
    timezone_name: str = "Europe/Moscow",
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
    m_settings = MasterSettings(
        master_id=master.id, studio_address="Test Address", hold_duration_minutes=30
    )
    session.add(m_settings)
    await session.flush()
    return master


async def _create_service(
    session: AsyncSession,
    master_id: int,
    title: str = "Service 1",
    price: Decimal = Decimal("2500.00"),
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
) -> Appointment:
    app = Appointment(
        master_id=master_id,
        user_id=user_id,
        service_id=service.id,
        status=status,
        start_time=start_time,
        end_time=start_time + timedelta(minutes=service.duration_min),
        end_time_with_buffer=start_time
        + timedelta(minutes=service.duration_min + service.buffer_min),
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


async def _create_payment_with_proof(
    session: AsyncSession,
    master_id: int,
    appointment_id: int,
    user_id: int,
    amount: Decimal = Decimal("500.00"),
) -> Payment:
    payment = Payment(
        master_id=master_id,
        appointment_id=appointment_id,
        user_id=user_id,
        amount=amount,
        status=PaymentStatus.SUBMITTED,
    )
    session.add(payment)
    await session.flush()
    proof = PaymentProof(
        payment_id=payment.id,
        telegram_file_id="proof_file_123",
        telegram_file_unique_id="unique_file_123",
        media_type=MediaType.PHOTO,
    )
    session.add(proof)
    await session.flush()
    return payment


# ---------------------------------------------------------------------------
# Test Scenarios
# ---------------------------------------------------------------------------

@requires_postgres
@pytest.mark.asyncio
async def test_owner_a_gets_admin_access_to_a(pg_session: AsyncSession):
    """Scenario 1: Owner A gets full admin access to Master A."""
    user_a = await _create_user(pg_session, telegram_id=101, first_name="OwnerA")
    master_a = await _create_master(pg_session, owner_user_id=user_a.id, display_name="Master A")

    auth_svc = MasterAuthorizationService(pg_session)
    assert await auth_svc.is_admin(master_a.id, user_a.id) is True
    assert await auth_svc.is_owner(master_a.id, user_a.id) is True
    assert await auth_svc.get_role(master_a.id, user_a.id) == AdminRole.OWNER

    # require_admin and require_owner should not raise
    await auth_svc.require_admin(master_a.id, user_a.id)
    await auth_svc.require_owner(master_a.id, user_a.id)


@requires_postgres
@pytest.mark.asyncio
async def test_owner_a_does_not_get_admin_access_to_b(pg_session: AsyncSession):
    """Scenario 2: Owner A has NO admin or owner access to Master B."""
    user_a = await _create_user(pg_session, telegram_id=201, first_name="OwnerA")
    user_b = await _create_user(pg_session, telegram_id=202, first_name="OwnerB")
    master_a = await _create_master(pg_session, owner_user_id=user_a.id, display_name="Master A")
    master_b = await _create_master(pg_session, owner_user_id=user_b.id, display_name="Master B")

    auth_svc = MasterAuthorizationService(pg_session)
    assert await auth_svc.is_admin(master_b.id, user_a.id) is False
    assert await auth_svc.is_owner(master_b.id, user_a.id) is False
    assert await auth_svc.get_role(master_b.id, user_a.id) == AdminRole.NONE

    with pytest.raises(AccessDeniedError):
        await auth_svc.require_admin(master_b.id, user_a.id)

    with pytest.raises(AccessDeniedError):
        await auth_svc.require_owner(master_b.id, user_a.id)


@requires_postgres
@pytest.mark.asyncio
async def test_admin_a_gets_admin_access_to_a(pg_session: AsyncSession):
    """Scenario 3: Delegated admin for Master A gets admin access to A."""
    owner_a = await _create_user(pg_session, telegram_id=301, first_name="OwnerA")
    admin_a = await _create_user(pg_session, telegram_id=302, first_name="AdminA")
    master_a = await _create_master(pg_session, owner_user_id=owner_a.id, display_name="Master A")

    admin_repo = MasterAdminRepository(pg_session)
    await admin_repo.add_admin(
        master_id=master_a.id,
        user_id=admin_a.id,
        role="ADMIN",
        created_by_user_id=owner_a.id,
    )

    auth_svc = MasterAuthorizationService(pg_session)
    assert await auth_svc.is_admin(master_a.id, admin_a.id) is True
    assert await auth_svc.is_owner(master_a.id, admin_a.id) is False
    assert await auth_svc.get_role(master_a.id, admin_a.id) == AdminRole.ADMIN

    await auth_svc.require_admin(master_a.id, admin_a.id)


@requires_postgres
@pytest.mark.asyncio
async def test_admin_a_does_not_get_admin_access_to_b(pg_session: AsyncSession):
    """Scenario 4: Delegated admin for Master A has NO access to Master B."""
    owner_a = await _create_user(pg_session, telegram_id=401, first_name="OwnerA")
    admin_a = await _create_user(pg_session, telegram_id=402, first_name="AdminA")
    owner_b = await _create_user(pg_session, telegram_id=403, first_name="OwnerB")

    master_a = await _create_master(pg_session, owner_user_id=owner_a.id, display_name="Master A")
    master_b = await _create_master(pg_session, owner_user_id=owner_b.id, display_name="Master B")

    admin_repo = MasterAdminRepository(pg_session)
    await admin_repo.add_admin(
        master_id=master_a.id,
        user_id=admin_a.id,
        role="ADMIN",
        created_by_user_id=owner_a.id,
    )

    auth_svc = MasterAuthorizationService(pg_session)
    assert await auth_svc.is_admin(master_b.id, admin_a.id) is False
    assert await auth_svc.get_role(master_b.id, admin_a.id) == AdminRole.NONE

    with pytest.raises(AccessDeniedError):
        await auth_svc.require_admin(master_b.id, admin_a.id)


@requires_postgres
@pytest.mark.asyncio
async def test_normal_client_a_does_not_get_admin_access(pg_session: AsyncSession):
    """Scenario 5: Regular client of Master A does not have admin privileges."""
    owner_a = await _create_user(pg_session, telegram_id=501, first_name="OwnerA")
    client_a = await _create_user(pg_session, telegram_id=502, first_name="ClientA")
    master_a = await _create_master(pg_session, owner_user_id=owner_a.id, display_name="Master A")

    # Add as client
    pg_session.add(MasterClient(master_id=master_a.id, user_id=client_a.id))
    await pg_session.flush()

    auth_svc = MasterAuthorizationService(pg_session)
    assert await auth_svc.is_admin(master_a.id, client_a.id) is False
    assert await auth_svc.is_owner(master_a.id, client_a.id) is False
    assert await auth_svc.get_role(master_a.id, client_a.id) == AdminRole.NONE

    with pytest.raises(AccessDeniedError):
        await auth_svc.require_admin(master_a.id, client_a.id)


@requires_postgres
@pytest.mark.asyncio
async def test_user_can_be_owner_a_and_client_b(pg_session: AsyncSession):
    """Scenario 6: User X is Owner of Master A and ordinary client of Master B."""
    user_x = await _create_user(pg_session, telegram_id=601, first_name="UserX")
    owner_b = await _create_user(pg_session, telegram_id=602, first_name="OwnerB")

    master_a = await _create_master(pg_session, owner_user_id=user_x.id, display_name="Master A")
    master_b = await _create_master(pg_session, owner_user_id=owner_b.id, display_name="Master B")

    # Client in Master B
    pg_session.add(MasterClient(master_id=master_b.id, user_id=user_x.id))
    await pg_session.flush()

    auth_svc = MasterAuthorizationService(pg_session)
    # Master A context: Owner
    assert await auth_svc.is_admin(master_a.id, user_x.id) is True
    assert await auth_svc.is_owner(master_a.id, user_x.id) is True
    assert await auth_svc.get_role(master_a.id, user_x.id) == AdminRole.OWNER

    # Master B context: Regular user (NONE)
    assert await auth_svc.is_admin(master_b.id, user_x.id) is False
    assert await auth_svc.is_owner(master_b.id, user_x.id) is False
    assert await auth_svc.get_role(master_b.id, user_x.id) == AdminRole.NONE


@requires_postgres
@pytest.mark.asyncio
async def test_user_can_be_owner_a_and_admin_b(pg_session: AsyncSession):
    """Scenario 7: User X is Owner of Master A and Delegated Admin of Master B."""
    user_x = await _create_user(pg_session, telegram_id=701, first_name="UserX")
    owner_b = await _create_user(pg_session, telegram_id=702, first_name="OwnerB")

    master_a = await _create_master(pg_session, owner_user_id=user_x.id, display_name="Master A")
    master_b = await _create_master(pg_session, owner_user_id=owner_b.id, display_name="Master B")

    admin_repo = MasterAdminRepository(pg_session)
    await admin_repo.add_admin(
        master_id=master_b.id,
        user_id=user_x.id,
        role="ADMIN",
        created_by_user_id=owner_b.id,
    )

    auth_svc = MasterAuthorizationService(pg_session)
    # Master A: OWNER
    assert await auth_svc.get_role(master_a.id, user_x.id) == AdminRole.OWNER
    assert await auth_svc.is_owner(master_a.id, user_x.id) is True

    # Master B: ADMIN (not OWNER)
    assert await auth_svc.get_role(master_b.id, user_x.id) == AdminRole.ADMIN
    assert await auth_svc.is_admin(master_b.id, user_x.id) is True
    assert await auth_svc.is_owner(master_b.id, user_x.id) is False


@requires_postgres
@pytest.mark.asyncio
async def test_disabled_master_admin_loses_access_immediately(pg_session: AsyncSession):
    """Scenario 8: Deactivating a MasterAdmin immediately revokes access."""
    owner_a = await _create_user(pg_session, telegram_id=801, first_name="OwnerA")
    admin_a = await _create_user(pg_session, telegram_id=802, first_name="AdminA")
    master_a = await _create_master(pg_session, owner_user_id=owner_a.id, display_name="Master A")

    admin_repo = MasterAdminRepository(pg_session)
    await admin_repo.add_admin(
        master_id=master_a.id,
        user_id=admin_a.id,
        role="ADMIN",
        created_by_user_id=owner_a.id,
    )

    auth_svc = MasterAuthorizationService(pg_session)
    assert await auth_svc.is_admin(master_a.id, admin_a.id) is True

    # Deactivate
    success = await admin_repo.deactivate_admin(master_a.id, admin_a.id)
    assert success is True

    # Access is immediately denied
    assert await auth_svc.is_admin(master_a.id, admin_a.id) is False
    assert await auth_svc.get_role(master_a.id, admin_a.id) == AdminRole.NONE

    with pytest.raises(AccessDeniedError):
        await auth_svc.require_admin(master_a.id, admin_a.id)


@requires_postgres
@pytest.mark.asyncio
async def test_old_callback_after_revoke_fails(pg_session: AsyncSession):
    """Scenario 9: Old callback query executed after admin rights revoked is rejected."""
    owner_a = await _create_user(pg_session, telegram_id=901, first_name="OwnerA")
    admin_a = await _create_user(pg_session, telegram_id=902, first_name="AdminA")
    master_a = await _create_master(pg_session, owner_user_id=owner_a.id, display_name="Master A")

    admin_repo = MasterAdminRepository(pg_session)
    await admin_repo.add_admin(
        master_id=master_a.id,
        user_id=admin_a.id,
        role="ADMIN",
        created_by_user_id=owner_a.id,
    )

    # Revoke
    await admin_repo.deactivate_admin(master_a.id, admin_a.id)

    # IsAdminFilter check
    admin_filter = IsAdminFilter()
    event_mock = MagicMock()
    event_mock.from_user.id = admin_a.telegram_id

    result = await admin_filter(
        event=event_mock,
        session=pg_session,
        master_id=master_a.id,
        db_user=admin_a,
    )
    assert result is False


@requires_postgres
@pytest.mark.asyncio
async def test_crafted_admin_callback_by_regular_user_is_rejected(pg_session: AsyncSession):
    """Scenario 10: Regular user attempting a crafted admin callback is rejected by IsAdminFilter."""
    owner_a = await _create_user(pg_session, telegram_id=1001, first_name="OwnerA")
    client_a = await _create_user(pg_session, telegram_id=1002, first_name="ClientA")
    master_a = await _create_master(pg_session, owner_user_id=owner_a.id, display_name="Master A")

    admin_filter = IsAdminFilter()
    event_mock = MagicMock()
    event_mock.from_user.id = client_a.telegram_id

    result = await admin_filter(
        event=event_mock,
        session=pg_session,
        master_id=master_a.id,
        db_user=client_a,
    )
    assert result is False


@requires_postgres
@pytest.mark.asyncio
async def test_owner_dynamically_resolves_from_master_owner_user_id(pg_session: AsyncSession):
    """Scenario 11: Changing Master.owner_user_id dynamically updates owner privileges."""
    user1 = await _create_user(pg_session, telegram_id=1101, first_name="User1")
    user2 = await _create_user(pg_session, telegram_id=1102, first_name="User2")
    master = await _create_master(pg_session, owner_user_id=user1.id, display_name="Master Dynamic")

    auth_svc = MasterAuthorizationService(pg_session)
    assert await auth_svc.is_owner(master.id, user1.id) is True
    assert await auth_svc.is_owner(master.id, user2.id) is False

    # Transfer ownership
    master.owner_user_id = user2.id
    await pg_session.flush()

    assert await auth_svc.is_owner(master.id, user1.id) is False
    assert await auth_svc.is_owner(master.id, user2.id) is True
    assert await auth_svc.is_admin(master.id, user2.id) is True


@requires_postgres
@pytest.mark.asyncio
async def test_admin_ids_in_env_does_not_grant_access_to_tenant(pg_session: AsyncSession):
    """Scenario 12: Telegram ID configured in settings.admin_ids has no access without tenant relationship."""
    random_user = await _create_user(pg_session, telegram_id=999888777, first_name="EnvAdmin")
    owner = await _create_user(pg_session, telegram_id=1201, first_name="RealOwner")
    master = await _create_master(pg_session, owner_user_id=owner.id, display_name="Isolated Master")

    # Simulate settings.admin_ids containing random_user's telegram_id
    original_admin_ids = list(settings.admin_ids)
    try:
        settings.admin_ids.append(random_user.telegram_id)

        auth_svc = MasterAuthorizationService(pg_session)
        assert await auth_svc.is_admin(master.id, random_user.id) is False

        admin_filter = IsAdminFilter()
        event_mock = MagicMock()
        event_mock.from_user.id = random_user.telegram_id

        result = await admin_filter(
            event=event_mock,
            session=pg_session,
            master_id=master.id,
            db_user=random_user,
        )
        assert result is False
    finally:
        settings.admin_ids = original_admin_ids


@requires_postgres
@pytest.mark.asyncio
async def test_legacy_admin_of_master_1_correctly_migrated_or_recognized(pg_session: AsyncSession):
    """Scenario 13: Master #1 owner correctly recognized and legacy admin row can be resolved."""
    owner_1 = await _create_user(pg_session, telegram_id=1301, first_name="LegacyOwner")
    master_1 = await _create_master(pg_session, owner_user_id=owner_1.id, display_name="Master 1")

    auth_svc = MasterAuthorizationService(pg_session)
    assert await auth_svc.is_admin(master_1.id, owner_1.id) is True
    assert await auth_svc.get_role(master_1.id, owner_1.id) == AdminRole.OWNER

    # Legacy admin resolution for FK
    user_repo = UserRepository(pg_session)
    legacy_admin = await user_repo.get_or_create_legacy_admin(owner_1.id)
    assert legacy_admin is not None
    assert legacy_admin.user_id == owner_1.id
    assert legacy_admin.is_active is True


@requires_postgres
@pytest.mark.asyncio
async def test_admin_b_cannot_edit_service_a(pg_session: AsyncSession):
    """Scenario 14: Admin B cannot toggle or edit Service A belonging to Master A."""
    owner_a = await _create_user(pg_session, telegram_id=1401, first_name="OwnerA")
    owner_b = await _create_user(pg_session, telegram_id=1402, first_name="OwnerB")
    master_a = await _create_master(pg_session, owner_user_id=owner_a.id, display_name="Master A")
    master_b = await _create_master(pg_session, owner_user_id=owner_b.id, display_name="Master B")

    service_a = await _create_service(pg_session, master_id=master_a.id, title="Service A")

    # Attempting to toggle/archive Service A within Master B's context fails
    service_repo = ServiceRepository(pg_session)
    assert await service_repo.toggle_active(service_a.id, master_id=master_b.id) is None
    assert await service_repo.archive(service_a.id, master_id=master_b.id) is False

    # Owner B has no admin access to Master A
    auth_svc = MasterAuthorizationService(pg_session)
    with pytest.raises(AccessDeniedError):
        await auth_svc.require_admin(master_a.id, owner_b.id)


@requires_postgres
@pytest.mark.asyncio
async def test_admin_b_cannot_approve_payment_a(pg_session: AsyncSession):
    """Scenario 15: Admin B cannot approve Payment A belonging to Master A."""
    owner_a = await _create_user(pg_session, telegram_id=1501, first_name="OwnerA")
    owner_b = await _create_user(pg_session, telegram_id=1502, first_name="OwnerB")
    client_a = await _create_user(pg_session, telegram_id=1503, first_name="ClientA")

    master_a = await _create_master(pg_session, owner_user_id=owner_a.id, display_name="Master A")
    master_b = await _create_master(pg_session, owner_user_id=owner_b.id, display_name="Master B")

    service_a = await _create_service(pg_session, master_id=master_a.id)
    app_a = await _create_appointment(
        pg_session,
        master_id=master_a.id,
        user_id=client_a.id,
        service=service_a,
        start_time=datetime(2026, 10, 10, 12, 0, tzinfo=timezone.utc),
        status=AppointmentStatus.PAYMENT_PROOF_SENT,
    )
    payment_a = await _create_payment_with_proof(
        pg_session, master_id=master_a.id, appointment_id=app_a.id, user_id=client_a.id
    )

    # Master B admin profile
    user_repo = UserRepository(pg_session)
    legacy_admin_b = await user_repo.get_or_create_legacy_admin(owner_b.id)

    payment_svc = PaymentService(pg_session)
    with pytest.raises(PaymentNotFoundError):
        await payment_svc.approve_payment(
            master_id=master_b.id,
            payment_id=payment_a.id,
            admin_id=legacy_admin_b.id,
        )

    auth_svc = MasterAuthorizationService(pg_session)
    with pytest.raises(AccessDeniedError):
        await auth_svc.require_admin(master_a.id, owner_b.id)


@requires_postgres
@pytest.mark.asyncio
async def test_admin_b_cannot_reschedule_appointment_a(pg_session: AsyncSession):
    """Scenario 16: Admin B cannot reschedule Appointment A belonging to Master A."""
    owner_a = await _create_user(pg_session, telegram_id=1601, first_name="OwnerA")
    owner_b = await _create_user(pg_session, telegram_id=1602, first_name="OwnerB")
    client_a = await _create_user(pg_session, telegram_id=1603, first_name="ClientA")

    master_a = await _create_master(pg_session, owner_user_id=owner_a.id, display_name="Master A")
    master_b = await _create_master(pg_session, owner_user_id=owner_b.id, display_name="Master B")

    service_a = await _create_service(pg_session, master_id=master_a.id)
    app_a = await _create_appointment(
        pg_session,
        master_id=master_a.id,
        user_id=client_a.id,
        service=service_a,
        start_time=datetime(2026, 10, 15, 14, 0, tzinfo=timezone.utc),
        status=AppointmentStatus.CONFIRMED,
    )

    user_repo = UserRepository(pg_session)
    legacy_admin_b = await user_repo.get_or_create_legacy_admin(owner_b.id)

    booking_svc = BookingService(pg_session)
    with pytest.raises(BookingNotFoundError):
        await booking_svc.reschedule_booking_by_admin(
            master_id=master_b.id,
            appointment_id=app_a.id,
            new_start_time=datetime(2026, 10, 16, 14, 0, tzinfo=timezone.utc),
            admin_id=legacy_admin_b.id,
        )

    auth_svc = MasterAuthorizationService(pg_session)
    with pytest.raises(AccessDeniedError):
        await auth_svc.require_admin(master_a.id, owner_b.id)


@requires_postgres
@pytest.mark.asyncio
async def test_single_telegram_id_does_not_have_platform_wide_access_across_masters(
    pg_session: AsyncSession,
):
    """Scenario 17: User who is admin in Master A has NO access in Master B or Master C."""
    owner_a = await _create_user(pg_session, telegram_id=1701, first_name="OwnerA")
    admin_a = await _create_user(pg_session, telegram_id=1702, first_name="AdminA")
    owner_b = await _create_user(pg_session, telegram_id=1703, first_name="OwnerB")
    owner_c = await _create_user(pg_session, telegram_id=1704, first_name="OwnerC")

    master_a = await _create_master(pg_session, owner_user_id=owner_a.id, display_name="Master A")
    master_b = await _create_master(pg_session, owner_user_id=owner_b.id, display_name="Master B")
    master_c = await _create_master(pg_session, owner_user_id=owner_c.id, display_name="Master C")

    admin_repo = MasterAdminRepository(pg_session)
    await admin_repo.add_admin(
        master_id=master_a.id,
        user_id=admin_a.id,
        role="ADMIN",
        created_by_user_id=owner_a.id,
    )

    auth_svc = MasterAuthorizationService(pg_session)
    assert await auth_svc.is_admin(master_a.id, admin_a.id) is True
    assert await auth_svc.is_admin(master_b.id, admin_a.id) is False
    assert await auth_svc.is_admin(master_c.id, admin_a.id) is False


@requires_postgres
@pytest.mark.asyncio
async def test_require_owner_allows_owner(pg_session: AsyncSession):
    """Scenario 18: require_owner succeeds when called for master owner."""
    owner = await _create_user(pg_session, telegram_id=1801, first_name="Owner")
    master = await _create_master(pg_session, owner_user_id=owner.id, display_name="Master")

    auth_svc = MasterAuthorizationService(pg_session)
    # Does not raise
    await auth_svc.require_owner(master.id, owner.id)


@requires_postgres
@pytest.mark.asyncio
async def test_require_owner_denies_regular_admin(pg_session: AsyncSession):
    """Scenario 19: require_owner raises AccessDeniedError for regular ADMIN."""
    owner = await _create_user(pg_session, telegram_id=1901, first_name="Owner")
    admin = await _create_user(pg_session, telegram_id=1902, first_name="Admin")
    master = await _create_master(pg_session, owner_user_id=owner.id, display_name="Master")

    admin_repo = MasterAdminRepository(pg_session)
    await admin_repo.add_admin(
        master_id=master.id,
        user_id=admin.id,
        role="ADMIN",
        created_by_user_id=owner.id,
    )

    auth_svc = MasterAuthorizationService(pg_session)
    # is_admin is True, but require_owner must fail
    assert await auth_svc.is_admin(master.id, admin.id) is True
    with pytest.raises(AccessDeniedError):
        await auth_svc.require_owner(master.id, admin.id)


@requires_postgres
@pytest.mark.asyncio
async def test_user_with_different_roles_across_a_b_c_gets_correct_role_per_master(
    pg_session: AsyncSession,
):
    """Scenario 20: User M is OWNER in A, ADMIN in B, and regular client in C.

    Verifies UserContextMiddleware injects correct context data per master.
    """
    user_m = await _create_user(pg_session, telegram_id=2001, first_name="UserM")
    owner_b = await _create_user(pg_session, telegram_id=2002, first_name="OwnerB")
    owner_c = await _create_user(pg_session, telegram_id=2003, first_name="OwnerC")

    master_a = await _create_master(pg_session, owner_user_id=user_m.id, display_name="Master A")
    master_b = await _create_master(pg_session, owner_user_id=owner_b.id, display_name="Master B")
    master_c = await _create_master(pg_session, owner_user_id=owner_c.id, display_name="Master C")

    # Delegated admin in B
    admin_repo = MasterAdminRepository(pg_session)
    await admin_repo.add_admin(
        master_id=master_b.id,
        user_id=user_m.id,
        role="ADMIN",
        created_by_user_id=owner_b.id,
    )

    # Client in C
    pg_session.add(MasterClient(master_id=master_c.id, user_id=user_m.id))
    await pg_session.flush()

    auth_svc = MasterAuthorizationService(pg_session)
    assert await auth_svc.get_role(master_a.id, user_m.id) == AdminRole.OWNER
    assert await auth_svc.get_role(master_b.id, user_m.id) == AdminRole.ADMIN
    assert await auth_svc.get_role(master_c.id, user_m.id) == AdminRole.NONE

    # Test UserContextMiddleware injection
    middleware = UserContextMiddleware()

    async def dummy_handler(event, data):
        return data

    tg_user_mock = MagicMock()
    tg_user_mock.id = user_m.telegram_id
    tg_user_mock.first_name = user_m.first_name
    tg_user_mock.last_name = None
    tg_user_mock.username = None

    # Context A
    data_a = {
        "event_from_user": tg_user_mock,
        "session": pg_session,
        "master_id": master_a.id,
        "bot_instance": SimpleNamespace(master_id=master_a.id),
    }
    result_a = await middleware(dummy_handler, MagicMock(), data_a)
    assert result_a["admin_role"] == AdminRole.OWNER
    assert result_a["is_admin"] is True
    assert result_a["is_owner"] is True

    # Context B
    data_b = {
        "event_from_user": tg_user_mock,
        "session": pg_session,
        "master_id": master_b.id,
        "bot_instance": SimpleNamespace(master_id=master_b.id),
    }
    result_b = await middleware(dummy_handler, MagicMock(), data_b)
    assert result_b["admin_role"] == AdminRole.ADMIN
    assert result_b["is_admin"] is True
    assert result_b["is_owner"] is False

    # Context C
    data_c = {
        "event_from_user": tg_user_mock,
        "session": pg_session,
        "master_id": master_c.id,
        "bot_instance": SimpleNamespace(master_id=master_c.id),
    }
    result_c = await middleware(dummy_handler, MagicMock(), data_c)
    assert result_c["admin_role"] == AdminRole.NONE
    assert result_c["is_admin"] is False
    assert result_c["is_owner"] is False


@requires_postgres
@pytest.mark.asyncio
async def test_get_admin_recipients_routing(pg_session: AsyncSession):
    """Scenario 21 (Bonus): Admin notifications route only to owner & active admins of current master."""
    owner_a = await _create_user(pg_session, telegram_id=2101, first_name="OwnerA")
    admin_a1 = await _create_user(pg_session, telegram_id=2102, first_name="AdminA1")
    admin_a2_inactive = await _create_user(pg_session, telegram_id=2103, first_name="AdminA2")
    owner_b = await _create_user(pg_session, telegram_id=2104, first_name="OwnerB")

    master_a = await _create_master(pg_session, owner_user_id=owner_a.id, display_name="Master A")
    master_b = await _create_master(pg_session, owner_user_id=owner_b.id, display_name="Master B")

    admin_repo = MasterAdminRepository(pg_session)
    await admin_repo.add_admin(master_a.id, admin_a1.id, role="ADMIN", created_by_user_id=owner_a.id)
    await admin_repo.add_admin(master_a.id, admin_a2_inactive.id, role="ADMIN", created_by_user_id=owner_a.id)
    await admin_repo.deactivate_admin(master_a.id, admin_a2_inactive.id)

    auth_svc = MasterAuthorizationService(pg_session)
    recipients_a = await auth_svc.get_admin_recipients(master_a.id)

    # Should contain owner_a (2101) and admin_a1 (2102)
    assert set(recipients_a) == {2101, 2102}
    # Inactive admin_a2 (2103) and foreign owner_b (2104) are excluded
    assert 2103 not in recipients_a
    assert 2104 not in recipients_a
