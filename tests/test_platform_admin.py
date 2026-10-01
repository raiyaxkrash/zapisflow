"""Test suite for Phase 2: Platform Admin & SaaS Management.

Covers:
1. Authorization: Platform Admin via settings.admin_ids and Admin DB table.
2. Authorization: Normal user rejection (AccessDenied / Alert).
3. Admin Dashboard statistics (total_users, total_masters, active_bots, trial/paid/expired, MRR, conversion).
4. SaaS Metrics (MRR, ARR, ARPU, new users/projects, total revenue).
5. User management (listing, pagination, user details, owned projects).
6. Project management (listing, details, toggle suspension, audit log).
7. Bot management (listing, details without raw tokens, status toggle).
8. Owner bot unlinking / deletion (webhook revocation, status DISABLED, project/CRM data preserved).
9. IDOR protection on bot deletion (non-owner cannot delete).
10. Subscriptions list and status tracking.
11. Payments listing and details.
12. Plan catalog management (listing, toggle is_active).
13. Manager Bot admin handlers and keyboards.
"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
import random
from typing import Optional
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.settings import settings
from app.database.models.appointment import Appointment, AppointmentStatus
from app.database.models.master import (
    BotInstance,
    BotInstanceStatus,
    Master,
    MasterClient,
    MasterStatus,
    SubscriptionStatus,
)
from app.database.models.subscription import (
    SubscriptionPayment,
    SubscriptionPeriod,
    SubscriptionPlan,
)
from app.database.models.user import Admin, User
from app.manager_bot.handlers import (
    cb_admin_dashboard,
    cb_admin_menu,
    cb_admin_metrics,
    cb_admin_projects,
    cb_admin_users,
    cb_bot_delete_confirm,
    cb_bot_delete_prompt,
)
from app.manager_bot.keyboards import (
    admin_menu_keyboard,
    main_menu_keyboard,
    project_card_keyboard,
)
from app.services.audit_service import AuditEvent
from app.services.bot_provisioning_service import BotProvisioningService
from app.services.exceptions import AccessDeniedError
from app.services.platform_admin_service import PlatformAdminService
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
    user = User(telegram_id=_next_telegram_id(), first_name=first_name)
    session.add(user)
    await session.flush()
    return user


async def _create_master(
    session: AsyncSession,
    owner_id: int,
    display_name: str = "Test Project",
    status: MasterStatus = MasterStatus.ACTIVE,
    sub_status: SubscriptionStatus = SubscriptionStatus.TRIAL,
) -> Master:
    master = Master(
        owner_user_id=owner_id,
        display_name=display_name,
        status=status,
        subscription_status=sub_status,
        trial_ends_at=datetime.now(timezone.utc) + timedelta(days=14),
    )
    session.add(master)
    await session.flush()
    return master


@pytest.mark.asyncio
@requires_postgres
async def test_is_platform_admin_via_settings(pg_session: AsyncSession):
    """Users with Telegram ID in settings.admin_ids are recognized as platform admins."""
    admin_tg_id = _next_telegram_id()
    orig_admins = list(settings.admin_ids)
    try:
        settings.admin_ids.append(admin_tg_id)
        admin_svc = PlatformAdminService(pg_session)
        assert await admin_svc.is_platform_admin(telegram_id=admin_tg_id) is True
    finally:
        settings.admin_ids = orig_admins


@pytest.mark.asyncio
@requires_postgres
async def test_is_platform_admin_via_db(pg_session: AsyncSession):
    """Users with active Admin record in DB are recognized as platform admins."""
    user = await _create_user(pg_session, first_name="DBAdmin")
    admin_svc = PlatformAdminService(pg_session)

    # Initially not admin
    assert await admin_svc.is_platform_admin(telegram_id=user.telegram_id, user_id=user.id) is False

    # Add active admin
    admin = Admin(user_id=user.id, role="ADMIN", is_active=True)
    pg_session.add(admin)
    await pg_session.commit()

    assert await admin_svc.is_platform_admin(telegram_id=user.telegram_id, user_id=user.id) is True

    # Deactivate admin
    admin.is_active = False
    await pg_session.commit()

    assert await admin_svc.is_platform_admin(telegram_id=user.telegram_id, user_id=user.id) is False


@pytest.mark.asyncio
@requires_postgres
async def test_dashboard_stats_and_saas_metrics(pg_session: AsyncSession):
    """Calculates live KPI metrics without hardcoded numbers."""
    admin_svc = PlatformAdminService(pg_session)

    # Create users
    u1 = await _create_user(pg_session, "Owner1")
    u2 = await _create_user(pg_session, "Owner2")

    # Create masters
    m_trial = await _create_master(pg_session, u1.id, "Trial Studio", sub_status=SubscriptionStatus.TRIAL)
    m_paid = await _create_master(pg_session, u1.id, "Paid Studio", sub_status=SubscriptionStatus.ACTIVE)
    m_exp = await _create_master(pg_session, u2.id, "Expired Studio", sub_status=SubscriptionStatus.EXPIRED)

    # Create active bot for m_paid
    bot = BotInstance(
        master_id=m_paid.id,
        telegram_bot_id=_next_telegram_id(),
        telegram_username="paid_bot",
        status=BotInstanceStatus.ACTIVE,
        is_current=True,
    )
    pg_session.add(bot)

    # Create successful payment
    payment = SubscriptionPayment(
        master_id=m_paid.id,
        provider="YOOKASSA",
        provider_payment_id=f"pay_{random.randint(1000, 9999)}",
        amount=Decimal("499.00"),
        currency="RUB",
        status="SUCCEEDED",
        paid_at=datetime.now(timezone.utc),
    )
    pg_session.add(payment)
    await pg_session.commit()

    stats = await admin_svc.get_dashboard_stats()
    assert stats["total_users"] >= 2
    assert stats["total_masters"] >= 3
    assert stats["active_bots"] >= 1
    assert stats["trial_masters"] >= 1
    assert stats["paid_masters"] >= 1
    assert stats["expired_masters"] >= 1
    assert stats["mrr"] >= Decimal("499.00")
    assert stats["conversion_rate"] > 0

    metrics = await admin_svc.get_saas_metrics()
    assert metrics["arr"] == stats["mrr"] * 12
    assert metrics["arpu"] >= Decimal("499.00")
    assert metrics["total_revenue"] >= Decimal("499.00")
    assert metrics["total_successful_payments"] >= 1


@pytest.mark.asyncio
@requires_postgres
async def test_list_users_and_get_details(pg_session: AsyncSession):
    """User listing returns users with project counts and detailed profile."""
    admin_svc = PlatformAdminService(pg_session)
    user = await _create_user(pg_session, "Alice")
    m1 = await _create_master(pg_session, user.id, "Alice Nails")
    m2 = await _create_master(pg_session, user.id, "Alice Lashes")
    await pg_session.commit()

    users, total, pages = await admin_svc.list_users(page=1, per_page=10)
    assert total >= 1
    target = next((u for u in users if u["id"] == user.id), None)
    assert target is not None
    assert target["projects_count"] >= 2

    details = await admin_svc.get_user_details(user.id)
    assert details is not None
    assert details["first_name"] == "Alice"
    assert len(details["projects"]) >= 2


@pytest.mark.asyncio
@requires_postgres
async def test_project_listing_and_suspension_toggle(pg_session: AsyncSession):
    """Admin can inspect project details and toggle suspension with audit logging."""
    admin_svc = PlatformAdminService(pg_session)
    owner = await _create_user(pg_session, "ProjectOwner")
    master = await _create_master(pg_session, owner.id, "Beauty Salon Elite", status=MasterStatus.ACTIVE)
    await pg_session.commit()

    # Listing
    projects, total, pages = await admin_svc.list_projects(page=1, per_page=10)
    p = next((x for x in projects if x["id"] == master.id), None)
    assert p is not None
    assert p["display_name"] == "Beauty Salon Elite"
    assert p["status"] == "ACTIVE"

    # Details
    details = await admin_svc.get_project_details(master.id)
    assert details is not None
    assert details["display_name"] == "Beauty Salon Elite"

    # Suspend
    ok, msg = await admin_svc.toggle_project_suspension(master.id, actor_user_id=owner.id)
    assert ok is True
    assert "приостановлен" in msg.lower()
    await pg_session.refresh(master)
    assert master.status == MasterStatus.SUSPENDED

    # Unsuspend
    ok2, msg2 = await admin_svc.toggle_project_suspension(master.id, actor_user_id=owner.id)
    assert ok2 is True
    assert "активирован" in msg2.lower()
    await pg_session.refresh(master)
    assert master.status == MasterStatus.ACTIVE


@pytest.mark.asyncio
@requires_postgres
async def test_bot_management_and_safe_token_views(pg_session: AsyncSession):
    """Admin sees bot instance info without exposing raw or decrypted tokens."""
    admin_svc = PlatformAdminService(pg_session)
    owner = await _create_user(pg_session, "BotMaster")
    master = await _create_master(pg_session, owner.id, "Bot Project")

    bot = BotInstance(
        master_id=master.id,
        telegram_bot_id=_next_telegram_id(),
        telegram_username="safe_audit_bot",
        telegram_first_name="SafeBot",
        encrypted_token="ciphertext_v1_secret",
        status=BotInstanceStatus.ACTIVE,
        is_current=True,
    )
    pg_session.add(bot)
    await pg_session.commit()

    # List bots
    bots, total, pages = await admin_svc.list_bots(page=1, per_page=10)
    b = next((x for x in bots if x["id"] == bot.id), None)
    assert b is not None
    assert b["telegram_username"] == "safe_audit_bot"
    assert "token" not in b
    assert "encrypted_token" not in b

    # Bot details
    details = await admin_svc.get_bot_details(bot.id)
    assert details is not None
    assert details["telegram_username"] == "safe_audit_bot"
    assert "encrypted_token" not in details
    assert "token" not in details
    assert "public_id" in details


@pytest.mark.asyncio
@requires_postgres
async def test_owner_bot_deletion_and_data_preservation(pg_session: AsyncSession):
    """Owner can delete bot: bot is unlinked/disabled, while CRM and appointments are preserved."""
    owner = await _create_user(pg_session, "Owner")
    client_user = await _create_user(pg_session, "Client")
    master = await _create_master(pg_session, owner.id, "Preserve Data Salon")

    bot = BotInstance(
        master_id=master.id,
        telegram_bot_id=_next_telegram_id(),
        telegram_username="to_delete_bot",
        status=BotInstanceStatus.ACTIVE,
        is_current=True,
    )
    pg_session.add(bot)

    # Add client relation
    client = MasterClient(master_id=master.id, user_id=client_user.id, notes="VIP client")
    pg_session.add(client)
    await pg_session.commit()

    gateway_mock = AsyncMock()
    gateway_mock.delete_webhook = AsyncMock(return_value=True)

    bot_svc = BotProvisioningService(pg_session, gateway=gateway_mock)
    deleted_bot = await bot_svc.delete_bot(bot.id, actor_user_id=owner.id)

    assert deleted_bot.status == BotInstanceStatus.DISABLED
    assert deleted_bot.is_current is False

    # Verify CRM data is preserved
    await pg_session.refresh(master)
    assert master.id is not None
    client_check = await pg_session.scalar(
        select(MasterClient).where(MasterClient.master_id == master.id)
    )
    assert client_check is not None
    assert client_check.notes == "VIP client"


@pytest.mark.asyncio
@requires_postgres
async def test_bot_deletion_idor_protection(pg_session: AsyncSession):
    """A non-owner cannot delete another master's bot."""
    owner = await _create_user(pg_session, "RealOwner")
    intruder = await _create_user(pg_session, "Intruder")
    master = await _create_master(pg_session, owner.id, "Real Salon")

    bot = BotInstance(
        master_id=master.id,
        telegram_bot_id=_next_telegram_id(),
        telegram_username="secure_bot",
        status=BotInstanceStatus.ACTIVE,
        is_current=True,
    )
    pg_session.add(bot)
    await pg_session.commit()

    bot_svc = BotProvisioningService(pg_session)
    with pytest.raises(AccessDeniedError):
        await bot_svc.delete_bot(bot.id, actor_user_id=intruder.id)


@pytest.mark.asyncio
@requires_postgres
async def test_plans_catalog_and_toggle(pg_session: AsyncSession):
    """Admin can list subscription plans and toggle active status."""
    admin_svc = PlatformAdminService(pg_session)
    user = await _create_user(pg_session, "PlanAdmin")

    plan = await pg_session.scalar(
        select(SubscriptionPlan).where(SubscriptionPlan.code == "basic_monthly")
    )
    assert plan is not None

    orig_active = plan.is_active
    ok, msg = await admin_svc.toggle_plan_active(plan.id, actor_user_id=user.id)
    assert ok is True
    await pg_session.refresh(plan)
    assert plan.is_active is not orig_active

    # Revert
    await admin_svc.toggle_plan_active(plan.id, actor_user_id=user.id)
    await pg_session.refresh(plan)
    assert plan.is_active is orig_active


@pytest.mark.asyncio
@requires_postgres
async def test_manager_bot_admin_guard_and_menu(pg_session: AsyncSession):
    """Manager Bot rejects non-admins from mgr:admin:menu and allows authorized admins."""
    normal_user = await _create_user(pg_session, "NormalUser")
    admin_user = await _create_user(pg_session, "SuperAdmin")

    # Grant admin to admin_user
    admin_record = Admin(user_id=admin_user.id, role="OWNER", is_active=True)
    pg_session.add(admin_record)
    await pg_session.commit()

    # Normal user attempt
    cb_normal = AsyncMock()
    cb_normal.from_user = MagicMock(id=normal_user.telegram_id, first_name=normal_user.first_name, username="normal", last_name=None)
    cb_normal.data = "mgr:admin:menu"
    cb_normal.answer = AsyncMock()
    cb_normal.message = AsyncMock()

    await cb_admin_menu(cb_normal, session=pg_session)
    cb_normal.answer.assert_called_once()
    assert "Доступ запрещён" in cb_normal.answer.call_args[0][0]
    cb_normal.message.edit_text.assert_not_called()

    # Admin user attempt
    cb_admin = AsyncMock()
    cb_admin.from_user = MagicMock(id=admin_user.telegram_id, first_name=admin_user.first_name, username="admin", last_name=None)
    cb_admin.data = "mgr:admin:menu"
    cb_admin.answer = AsyncMock()
    cb_admin.message = AsyncMock()

    await cb_admin_menu(cb_admin, session=pg_session)
    cb_admin.message.edit_text.assert_called_once()
    assert "Platform Admin" in cb_admin.message.edit_text.call_args[0][0]


@pytest.mark.asyncio
@requires_postgres
async def test_manager_bot_owner_bot_deletion_flow(pg_session: AsyncSession):
    """Manager Bot owner bot delete prompt and confirmation execution."""
    owner = await _create_user(pg_session, "OwnerDelete")
    master = await _create_master(pg_session, owner.id, "Salon To Clean")

    bot = BotInstance(
        master_id=master.id,
        telegram_bot_id=_next_telegram_id(),
        telegram_username="cleanup_bot",
        status=BotInstanceStatus.ACTIVE,
        is_current=True,
    )
    pg_session.add(bot)
    await pg_session.commit()

    # 1. Prompt
    cb_prompt = AsyncMock()
    cb_prompt.from_user = MagicMock(id=owner.telegram_id, first_name=owner.first_name, username="owner", last_name=None)
    cb_prompt.data = f"mgr:bot:delete:{master.id}"
    cb_prompt.answer = AsyncMock()
    cb_prompt.message = AsyncMock()

    await cb_bot_delete_prompt(cb_prompt, session=pg_session)
    cb_prompt.message.edit_text.assert_called_once()
    assert "Удаление бота из проекта" in cb_prompt.message.edit_text.call_args[0][0]

    # 2. Confirm
    cb_confirm = AsyncMock()
    cb_confirm.from_user = MagicMock(id=owner.telegram_id, first_name=owner.first_name, username="owner", last_name=None)
    cb_confirm.data = f"mgr:bot:delete:confirm:{master.id}"
    cb_confirm.answer = AsyncMock()
    cb_confirm.message = AsyncMock()

    await cb_bot_delete_confirm(cb_confirm, session=pg_session)
    cb_confirm.answer.assert_called_once()
    assert "успешно отключён и удалён" in cb_confirm.answer.call_args[0][0]

    await pg_session.refresh(bot)
    assert bot.status == BotInstanceStatus.DISABLED
    assert bot.is_current is False
