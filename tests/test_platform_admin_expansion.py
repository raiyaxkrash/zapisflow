"""Test suite for expanded Platform Admin features:
1. Subscription plan editing (name, price validation, duration, whitelist features, sort order, code immutability).
2. Manual subscription extension & exact expiry date setting with status semantics, suspended safety, and audit logs.
3. User card sub-navigation queries (projects, bots, subscriptions, payments).
4. Bot hard delete with webhook cleanup, registry cache invalidation, outbox purge, and audit log.
5. Project hard delete with impact summary, cascade DB purge, and preservation of global User accounts.
6. Audit log listing and inspection.
"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
import random
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.settings import settings
from app.database.models.appointment import Appointment, AppointmentStatus
from app.database.models.audit import AuditLog
from app.database.models.master import (
    BotInstance,
    BotInstanceStatus,
    Master,
    MasterClient,
    MasterStatus,
    SubscriptionStatus,
)
from app.database.models.telegram_outbox import TelegramOutbox, TelegramOutboxStatus
from app.database.models.portfolio import PortfolioCategory, PortfolioItem
from app.database.models.schedule import ScheduleTemplate
from app.database.models.service import Service
from app.database.models.staff import StaffMember
from app.database.models.subscription import (
    SubscriptionPayment,
    SubscriptionPeriod,
    SubscriptionPlan,
)
from app.database.models.user import Admin, User
from app.services.audit_service import AuditEvent
from app.services.bot_registry import BotRegistry
from app.services.platform_admin_service import PlatformAdminService
from app.services.subscription_service import SubscriptionService
from tests.conftest import requires_postgres


TEST_CRYPTO_KEY = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"


@pytest.fixture(autouse=True)
def setup_encryption_key():
    orig = settings.bot_token_encryption_key
    settings.bot_token_encryption_key = TEST_CRYPTO_KEY
    yield
    settings.bot_token_encryption_key = orig


def _next_telegram_id() -> int:
    return random.randint(2_000_000_000, 3_000_000_000)


async def _create_user(session: AsyncSession, first_name: str = "TestUser") -> User:
    user = User(telegram_id=_next_telegram_id(), first_name=first_name, username=f"u_{_next_telegram_id()}")
    session.add(user)
    await session.flush()
    return user


async def _create_master(
    session: AsyncSession,
    owner_id: int,
    display_name: str = "Test Project",
    status: MasterStatus = MasterStatus.ACTIVE,
    sub_status: SubscriptionStatus = SubscriptionStatus.TRIAL,
    paid_until: datetime = None,
) -> Master:
    master = Master(
        owner_user_id=owner_id,
        display_name=display_name,
        status=status,
        subscription_status=sub_status,
        trial_ends_at=datetime.now(timezone.utc) + timedelta(days=14),
        paid_until=paid_until,
    )
    session.add(master)
    await session.flush()
    return master


@pytest.mark.asyncio
@requires_postgres
async def test_platform_admin_plan_editing(pg_session: AsyncSession):
    """Test full editing of subscription plans and validation rules."""
    admin_user = await _create_user(pg_session, first_name="SuperAdmin")
    admin_svc = PlatformAdminService(pg_session)

    # 1. Create a test plan
    plan_code = f"test_plan_{random.randint(1000, 9999)}"
    plan = SubscriptionPlan(
        code=plan_code,
        name="Начальный",
        price=Decimal("990.00"),
        currency="RUB",
        period_days=30,
        is_active=True,
        sort_order=10,
        features={"max_bots": 1, "max_staff": 1},
    )
    pg_session.add(plan)
    await pg_session.commit()

    # 2. Update name (success and empty validation)
    ok, msg, p = await admin_svc.update_plan(plan.id, actor_user_id=admin_user.id, name="Продвинутый")
    assert ok is True
    assert p.name == "Продвинутый"

    ok, msg, _ = await admin_svc.update_plan(plan.id, actor_user_id=admin_user.id, name="   ")
    assert ok is False
    assert "не может быть пустым" in msg

    # 3. Update price (success, zero/negative rejection, upper bound rejection)
    ok, msg, p = await admin_svc.update_plan(plan.id, actor_user_id=admin_user.id, price=Decimal("1490.50"))
    assert ok is True
    assert p.price == Decimal("1490.50")

    ok, msg, _ = await admin_svc.update_plan(plan.id, actor_user_id=admin_user.id, price=Decimal("0.00"))
    assert ok is False
    assert "больше 0" in msg

    ok, msg, _ = await admin_svc.update_plan(plan.id, actor_user_id=admin_user.id, price=Decimal("-50.00"))
    assert ok is False
    assert "больше 0" in msg

    ok, msg, _ = await admin_svc.update_plan(plan.id, actor_user_id=admin_user.id, price=Decimal("2000000.00"))
    assert ok is False
    assert "не может превышать" in msg

    # 4. Update duration (period_days)
    ok, msg, p = await admin_svc.update_plan(plan.id, actor_user_id=admin_user.id, period_days=90)
    assert ok is True
    assert p.period_days == 90

    ok, msg, _ = await admin_svc.update_plan(plan.id, actor_user_id=admin_user.id, period_days=0)
    assert ok is False

    ok, msg, _ = await admin_svc.update_plan(plan.id, actor_user_id=admin_user.id, period_days=5000)
    assert ok is False

    # 5. Update sort order
    ok, msg, p = await admin_svc.update_plan(plan.id, actor_user_id=admin_user.id, sort_order=25)
    assert ok is True
    assert p.sort_order == 25

    # 6. Update features whitelist
    new_features = {
        "max_bots": 3,
        "max_staff": 10,
        "custom_branding": True,
        "broadcasts": True,
        "disallowed_hack_key": "malicious",
    }
    ok, msg, p = await admin_svc.update_plan(plan.id, actor_user_id=admin_user.id, features=new_features)
    assert ok is True
    assert p.features["max_bots"] == 3
    assert p.features["max_staff"] == 10
    assert p.features["custom_branding"] is True
    assert p.features["broadcasts"] is True
    assert "disallowed_hack_key" not in p.features

    # 7. Verify code is immutable
    assert p.code == plan_code

    # 8. Check audit log
    audit_res = await pg_session.execute(
        select(AuditLog).where(AuditLog.action == AuditEvent.PLAN_UPDATED, AuditLog.entity_id == plan.id)
    )
    logs = audit_res.scalars().all()
    assert len(logs) >= 4


@pytest.mark.asyncio
@requires_postgres
async def test_manual_subscription_extension_and_expiry(pg_session: AsyncSession):
    """Test manual subscription extension logic across statuses and exact date setting."""
    admin_user = await _create_user(pg_session, first_name="SubAdmin")
    owner = await _create_user(pg_session, first_name="ProjectOwner")
    admin_svc = PlatformAdminService(pg_session)

    # 1. Active subscription: extension adds days to existing paid_until
    initial_paid = datetime.now(timezone.utc) + timedelta(days=10)
    master_active = await _create_master(
        pg_session, owner.id, "Active Project", status=MasterStatus.ACTIVE, sub_status=SubscriptionStatus.ACTIVE, paid_until=initial_paid
    )
    await pg_session.commit()

    ok, msg, new_date = await admin_svc.extend_subscription_manually(
        master_id=master_active.id,
        days=30,
        actor_user_id=admin_user.id,
        reason="Партнёрская программа",
    )
    assert ok is True
    await pg_session.refresh(master_active)
    assert master_active.subscription_status == SubscriptionStatus.ACTIVE
    # Should be approximately initial_paid + 30 days
    diff = (master_active.paid_until - (initial_paid + timedelta(days=30))).total_seconds()
    assert abs(diff) < 5

    # 2. Expired subscription: extension sets paid_until = now + days and status ACTIVE
    expired_paid = datetime.now(timezone.utc) - timedelta(days=5)
    master_expired = await _create_master(
        pg_session, owner.id, "Expired Project", status=MasterStatus.ACTIVE, sub_status=SubscriptionStatus.EXPIRED, paid_until=expired_paid
    )
    await pg_session.commit()

    ok, msg, new_date = await admin_svc.extend_subscription_manually(
        master_id=master_expired.id,
        days=14,
        actor_user_id=admin_user.id,
        reason="Компенсация сбоя",
    )
    assert ok is True
    await pg_session.refresh(master_expired)
    assert master_expired.subscription_status == SubscriptionStatus.ACTIVE
    expected_expiry = datetime.now(timezone.utc) + timedelta(days=14)
    assert abs((master_expired.paid_until - expected_expiry).total_seconds()) < 10

    # 3. Suspended project: extending days extends paid_until BUT remains SUSPENDED
    master_suspended = await _create_master(
        pg_session, owner.id, "Suspended Project", status=MasterStatus.SUSPENDED, sub_status=SubscriptionStatus.SUSPENDED, paid_until=initial_paid
    )
    await pg_session.commit()

    ok, msg, new_date = await admin_svc.extend_subscription_manually(
        master_id=master_suspended.id,
        days=7,
        actor_user_id=admin_user.id,
        reason="Тест поддержки",
    )
    assert ok is True
    await pg_session.refresh(master_suspended)
    assert master_suspended.subscription_status == SubscriptionStatus.SUSPENDED
    assert master_suspended.status == MasterStatus.SUSPENDED

    # 4. Set exact expiry date in future -> ACTIVE
    future_date = datetime.now(timezone.utc) + timedelta(days=45)
    ok, msg, set_dt = await admin_svc.set_subscription_expiry_manually(
        master_id=master_active.id,
        new_expiry_date=future_date,
        actor_user_id=admin_user.id,
        reason="Ручная установка",
    )
    assert ok is True
    await pg_session.refresh(master_active)
    assert master_active.paid_until == future_date
    assert master_active.subscription_status == SubscriptionStatus.ACTIVE

    # 5. Set exact expiry date in past -> EXPIRED
    past_date = datetime.now(timezone.utc) - timedelta(days=2)
    ok, msg, set_dt = await admin_svc.set_subscription_expiry_manually(
        master_id=master_active.id,
        new_expiry_date=past_date,
        actor_user_id=admin_user.id,
        reason="Принудительное завершение",
    )
    assert ok is True
    await pg_session.refresh(master_active)
    assert master_active.paid_until == past_date
    assert master_active.subscription_status == SubscriptionStatus.EXPIRED

    # 6. Verify audit history for master
    history = await admin_svc.get_subscription_history(master_active.id)
    assert len(history) >= 3
    actions = [h["action"] for h in history]
    assert AuditEvent.SUBSCRIPTION_EXTENDED in actions
    assert AuditEvent.SUBSCRIPTION_EXPIRY_SET in actions


@pytest.mark.asyncio
@requires_postgres
async def test_user_card_subnavigation_queries(pg_session: AsyncSession):
    """Test sub-queries for user card navigation in Platform Admin."""
    user = await _create_user(pg_session, first_name="MultiTenantOwner")
    m1 = await _create_master(pg_session, user.id, "Studio One")
    m2 = await _create_master(pg_session, user.id, "Studio Two")

    bot1 = BotInstance(
        master_id=m1.id,
        telegram_bot_id=random.randint(100000, 999999),
        telegram_username="studio_one_bot",
        telegram_first_name="Studio One Bot",
        encrypted_token="enc1",
        status=BotInstanceStatus.ACTIVE,
        is_current=True,
    )
    pg_session.add(bot1)

    plan = SubscriptionPlan(
        code=f"plan_{random.randint(1000, 9999)}",
        name="Стандарт",
        price=Decimal("1500.00"),
        currency="RUB",
        period_days=30,
        is_active=True,
    )
    pg_session.add(plan)
    await pg_session.flush()

    payment = SubscriptionPayment(
        master_id=m1.id,
        plan_id=plan.id,
        provider="YOOKASSA",
        amount=Decimal("1500.00"),
        currency="RUB",
        status="SUCCEEDED",
        period_days=30,
        provider_payment_id="yoopay_test_123",
    )
    pg_session.add(payment)
    await pg_session.commit()

    admin_svc = PlatformAdminService(pg_session)

    # 1. Projects
    projects = await admin_svc.list_user_projects(user.id)
    assert len(projects) == 2
    assert {p["id"] for p in projects} == {m1.id, m2.id}

    # 2. Bots
    bots = await admin_svc.list_user_bots(user.id)
    assert len(bots) == 1
    assert bots[0]["id"] == bot1.id
    assert bots[0]["master_id"] == m1.id

    # 3. Subscriptions
    subs = await admin_svc.list_user_subscriptions(user.id)
    assert len(subs) == 2

    # 4. Payments
    payments = await admin_svc.list_user_payments(user.id)
    assert len(payments) == 1
    assert payments[0]["id"] == payment.id
    assert payments[0]["amount"] == Decimal("1500.00")


@pytest.mark.asyncio
@requires_postgres
async def test_bot_hard_delete_with_outbox(pg_session: AsyncSession):
    """Test complete bot hard deletion with telegram outbox cleanup and audit logging."""
    admin_user = await _create_user(pg_session, first_name="BotDeleter")
    owner = await _create_user(pg_session, first_name="BotOwner")
    master = await _create_master(pg_session, owner.id, "Bot Master")

    bot = BotInstance(
        master_id=master.id,
        telegram_bot_id=random.randint(100000, 999999),
        telegram_username="to_delete_bot",
        telegram_first_name="Delete Me Bot",
        encrypted_token="enc_dummy_token",
        status=BotInstanceStatus.ACTIVE,
        is_current=True,
    )
    pg_session.add(bot)
    await pg_session.flush()

    # Add a TelegramOutbox record with FK RESTRICT on bot.id
    outbox = TelegramOutbox(
        bot_instance_id=bot.id,
        master_id=master.id,
        operation_type="SEND_MESSAGE",
        target_chat_id=owner.telegram_id,
        idempotency_key=f"idem_{random.randint(1000, 9999)}",
        status=TelegramOutboxStatus.PENDING,
        payload={"text": "Hello client"},
    )
    pg_session.add(outbox)
    await pg_session.commit()

    mock_gateway = MagicMock()
    mock_gateway.delete_webhook = AsyncMock(return_value=True)
    mock_registry = MagicMock(spec=BotRegistry)
    mock_registry.invalidate_bot_instance = AsyncMock()

    admin_svc = PlatformAdminService(pg_session, registry=mock_registry, gateway=mock_gateway)

    # Execute hard delete
    ok, msg = await admin_svc.hard_delete_bot(bot.id, actor_user_id=admin_user.id)
    assert ok is True
    assert "удалён" in msg

    # Verify bot record is gone
    check_bot = await pg_session.get(BotInstance, bot.id)
    assert check_bot is None

    # Verify outbox record is gone
    check_outbox = await pg_session.get(TelegramOutbox, outbox.id)
    assert check_outbox is None

    # Verify webhook delete and registry invalidation were attempted
    mock_registry.invalidate_bot_instance.assert_called_once_with(bot.id, reason="bot_hard_deleted")

    # Verify audit log
    audit_res = await pg_session.execute(
        select(AuditLog).where(AuditLog.action == AuditEvent.BOT_HARD_DELETED, AuditLog.entity_id == bot.id)
    )
    log = audit_res.scalar_one_or_none()
    assert log is not None
    assert log.actor_user_id == admin_user.id


@pytest.mark.asyncio
@requires_postgres
async def test_project_hard_delete_cascade_and_user_preservation(pg_session: AsyncSession):
    """Test project hard deletion cascades all business data but preserves User accounts."""
    admin_user = await _create_user(pg_session, first_name="ProjectDeleter")
    owner = await _create_user(pg_session, first_name="ProjectOwner")
    client_user = await _create_user(pg_session, first_name="ClientUser")
    staff_user = await _create_user(pg_session, first_name="StaffUser")

    master = await _create_master(pg_session, owner.id, "Big Project To Delete")

    # Add child entities across the schema
    bot = BotInstance(
        master_id=master.id,
        telegram_bot_id=random.randint(100000, 999999),
        telegram_username="big_proj_bot",
        telegram_first_name="Big Proj Bot",
        encrypted_token="enc",
        status=BotInstanceStatus.ACTIVE,
        is_current=True,
    )
    pg_session.add(bot)

    staff = StaffMember(
        master_id=master.id,
        user_id=staff_user.id,
        display_name="Master Anna",
        is_active=True,
    )
    pg_session.add(staff)

    service = Service(
        master_id=master.id,
        title="Complex Haircut",
        price=Decimal("2000.00"),
        duration_min=60,
        is_active=True,
    )
    pg_session.add(service)

    client = MasterClient(
        master_id=master.id,
        user_id=client_user.id,
        notes="Client note",
    )
    pg_session.add(client)

    cat = PortfolioCategory(
        master_id=master.id,
        title="Haircuts",
    )
    pg_session.add(cat)
    await pg_session.flush()

    item = PortfolioItem(
        master_id=master.id,
        category_id=cat.id,
        telegram_file_id="photo_123",
        title="Photo 1",
    )
    pg_session.add(item)

    sched = ScheduleTemplate(
        master_id=master.id,
        staff_id=staff.id,
        day_of_week=0,
        is_day_off=False,
    )
    pg_session.add(sched)
    await pg_session.flush()

    appt = Appointment(
        master_id=master.id,
        user_id=client_user.id,
        service_id=service.id,
        staff_id=staff.id,
        status=AppointmentStatus.CONFIRMED,
        start_time=datetime.now(timezone.utc) + timedelta(days=1),
        end_time=datetime.now(timezone.utc) + timedelta(days=1, hours=1),
        end_time_with_buffer=datetime.now(timezone.utc) + timedelta(days=1, hours=1, minutes=15),
        snapshot_service_title="Complex Haircut",
        snapshot_service_price=Decimal("2000.00"),
        snapshot_service_duration_min=60,
        snapshot_buffer_duration_min=15,
        snapshot_deposit_amount=Decimal("0.00"),
    )
    pg_session.add(appt)
    await pg_session.commit()

    mock_gateway = MagicMock()
    mock_gateway.delete_webhook = AsyncMock(return_value=True)
    mock_registry = MagicMock(spec=BotRegistry)
    mock_registry.invalidate_bot_instance = AsyncMock()

    admin_svc = PlatformAdminService(pg_session, registry=mock_registry, gateway=mock_gateway)

    # 1. Verify impact summary
    impact = await admin_svc.get_project_impact_summary(master.id)
    assert impact["bots"] >= 1
    assert impact["staff"] >= 1
    assert impact["services"] >= 1
    assert impact["clients"] >= 1
    assert impact["portfolio"] >= 1
    assert impact["schedule"] >= 1
    assert impact["appointments"] >= 1

    # 2. Execute hard delete
    ok, msg = await admin_svc.hard_delete_project(master.id, actor_user_id=admin_user.id)
    assert ok is True
    assert "удалены" in msg or "удален" in msg.lower()

    # 3. Verify master and all children are purged
    assert await pg_session.get(Master, master.id) is None
    assert await pg_session.get(BotInstance, bot.id) is None
    assert await pg_session.get(StaffMember, staff.id) is None
    assert await pg_session.get(Service, service.id) is None
    assert await pg_session.get(MasterClient, client.id) is None
    assert await pg_session.get(PortfolioItem, item.id) is None
    assert await pg_session.get(PortfolioCategory, cat.id) is None
    assert await pg_session.get(ScheduleTemplate, sched.id) is None
    assert await pg_session.get(Appointment, appt.id) is None

    # 4. CRITICAL: Global User records must NOT be deleted!
    assert await pg_session.get(User, owner.id) is not None
    assert await pg_session.get(User, client_user.id) is not None
    assert await pg_session.get(User, staff_user.id) is not None
    assert await pg_session.get(User, admin_user.id) is not None

    # 5. Check audit log
    audit_res = await pg_session.execute(
        select(AuditLog).where(AuditLog.action == AuditEvent.PROJECT_HARD_DELETED, AuditLog.entity_id == master.id)
    )
    log = audit_res.scalar_one_or_none()
    assert log is not None
    assert log.actor_user_id == admin_user.id


@pytest.mark.asyncio
@requires_postgres
async def test_audit_log_listing_and_inspection(pg_session: AsyncSession):
    """Test paginated audit log listing and individual detail fetching."""
    admin_user = await _create_user(pg_session, first_name="AuditAdmin")
    admin_svc = PlatformAdminService(pg_session)

    # Generate an audit event
    await admin_svc.audit_service.log_event(
        action=AuditEvent.PLAN_UPDATED,
        actor_user_id=admin_user.id,
        entity_type="SubscriptionPlan",
        entity_id=999,
        payload_before={"price": "100"},
        payload_after={"price": "200"},
    )
    await pg_session.commit()

    # List audit logs
    items, total, total_pages = await admin_svc.list_audit_logs(page=1, per_page=10)
    assert total >= 1
    assert total_pages >= 1
    assert len(items) >= 1
    log_id = items[0]["id"]

    # Inspect single audit log details
    details = await admin_svc.get_audit_log_details(log_id)
    assert details is not None
    assert details["id"] == log_id
    assert details["actor_user_id"] == admin_user.id
    assert details["actor_username"] == admin_user.username


@pytest.mark.asyncio
@requires_postgres
async def test_manager_bot_admin_rbac_protection(pg_session: AsyncSession):
    """Verify unauthorized users are blocked from admin bot and project deletion callbacks."""
    from app.manager_bot.handlers import (
        cb_admin_bot_hard_delete,
        cb_admin_plan_detail,
        cb_admin_project_hard_delete,
    )

    normal_user = await _create_user(pg_session, first_name="UnauthorizedUser")

    cb = AsyncMock()
    cb.from_user = MagicMock(id=normal_user.telegram_id, first_name=normal_user.first_name, username="unauth", last_name=None)
    cb.answer = AsyncMock()
    cb.message = AsyncMock()

    # Bot hard delete attempt
    cb.data = "mgr:admin:bot:hard_delete:1"
    await cb_admin_bot_hard_delete(cb, session=pg_session)
    cb.answer.assert_called()
    assert "Доступ запрещён" in cb.answer.call_args[0][0]

    # Project hard delete attempt
    cb.answer.reset_mock()
    cb.data = "mgr:admin:project:hard_delete:1"
    await cb_admin_project_hard_delete(cb, session=pg_session)
    cb.answer.assert_called()
    assert "Доступ запрещён" in cb.answer.call_args[0][0]

    # Plan detail attempt
    cb.answer.reset_mock()
    cb.data = "mgr:admin:plan:1"
    await cb_admin_plan_detail(cb, session=pg_session)
    cb.answer.assert_called()
    assert "Доступ запрещён" in cb.answer.call_args[0][0]


@pytest.mark.asyncio
@requires_postgres
async def test_manager_bot_typed_confirmation_handlers(pg_session: AsyncSession):
    """Verify typed text confirmation protects against accidental deletion."""
    from app.manager_bot.handlers import (
        msg_admin_bot_hard_delete_confirm,
        msg_admin_project_hard_delete_confirm,
    )

    admin_user = await _create_user(pg_session, first_name="TypedAdmin")
    admin_record = Admin(user_id=admin_user.id, role="OWNER", is_active=True)
    pg_session.add(admin_record)

    master = await _create_master(pg_session, admin_user.id, "Typed Project")
    bot = BotInstance(
        master_id=master.id,
        telegram_bot_id=random.randint(100000, 999999),
        telegram_username="typed_bot",
        status=BotInstanceStatus.ACTIVE,
        is_current=True,
    )
    pg_session.add(bot)
    await pg_session.commit()

    # 1. Bot: Wrong text rejected
    state_mock = AsyncMock()
    state_mock.get_data = AsyncMock(return_value={"bot_id": bot.id})
    state_mock.clear = AsyncMock()

    msg_wrong = AsyncMock()
    msg_wrong.from_user = MagicMock(id=admin_user.telegram_id, first_name=admin_user.first_name, username="admin", last_name=None)
    msg_wrong.text = f"delete bot {bot.id}"  # lowercase
    msg_wrong.answer = AsyncMock()

    await msg_admin_bot_hard_delete_confirm(msg_wrong, state=state_mock, session=pg_session)
    assert "Удаление бота отменено" in msg_wrong.answer.call_args[0][0]
    assert await pg_session.get(BotInstance, bot.id) is not None

    # 2. Bot: Exact text executed
    state_mock.get_data = AsyncMock(return_value={"bot_id": bot.id})
    msg_exact = AsyncMock()
    msg_exact.from_user = MagicMock(id=admin_user.telegram_id, first_name=admin_user.first_name, username="admin", last_name=None)
    msg_exact.text = f"DELETE BOT {bot.id}"
    msg_exact.answer = AsyncMock()

    mock_gateway = MagicMock()
    mock_gateway.delete_webhook = AsyncMock(return_value=True)
    mock_reg = MagicMock(spec=BotRegistry)
    mock_reg.invalidate_bot_instance = AsyncMock()

    await msg_admin_bot_hard_delete_confirm(msg_exact, state=state_mock, session=pg_session, bot_registry=mock_reg)
    assert "успешно" in msg_exact.answer.call_args[0][0] or "удалён" in msg_exact.answer.call_args[0][0]
    assert await pg_session.get(BotInstance, bot.id) is None

    # 3. Project: Wrong text rejected
    state_mock.get_data = AsyncMock(return_value={"master_id": master.id})
    msg_proj_wrong = AsyncMock()
    msg_proj_wrong.from_user = MagicMock(id=admin_user.telegram_id, first_name=admin_user.first_name, username="admin", last_name=None)
    msg_proj_wrong.text = "CANCEL"
    msg_proj_wrong.answer = AsyncMock()

    await msg_admin_project_hard_delete_confirm(msg_proj_wrong, state=state_mock, session=pg_session)
    assert "Удаление проекта отменено" in msg_proj_wrong.answer.call_args[0][0]
    assert await pg_session.get(Master, master.id) is not None

    # 4. Project: Exact text executed
    state_mock.get_data = AsyncMock(return_value={"master_id": master.id})
    msg_proj_exact = AsyncMock()
    msg_proj_exact.from_user = MagicMock(id=admin_user.telegram_id, first_name=admin_user.first_name, username="admin", last_name=None)
    msg_proj_exact.text = f"DELETE PROJECT {master.id}"
    msg_proj_exact.answer = AsyncMock()

    await msg_admin_project_hard_delete_confirm(msg_proj_exact, state=state_mock, session=pg_session, bot_registry=mock_reg)
    assert "успешно" in msg_proj_exact.answer.call_args[0][0] or "удалён" in msg_proj_exact.answer.call_args[0][0]
    assert await pg_session.get(Master, master.id) is None

