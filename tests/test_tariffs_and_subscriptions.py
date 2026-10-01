"""Comprehensive test suite for ZapisFlow SaaS Tariffs, Subscriptions & Support System.

Tests all 18 key scenarios:
1. Primary tariff 'basic_monthly' in DB (499 RUB, 30 days, sort_order=1, active).
2. SubscriptionPlan model aliases (price_rub, duration_days).
3. SubscriptionService.get_active_plan() defaults to basic_monthly (499 RUB).
4. SubscriptionService.list_active_plans() ordered by sort_order.
5. PromoCode ORM persistence and metadata attributes.
6. Support settings configuration (SUPPORT_TELEGRAM_USERNAME, support_url, support_tag).
7. Manager Bot main menu keyboard has [💳 Подписка] and [🆘 Поддержка].
8. Manager Bot subscription menu handler with 0 masters (prompt to create project).
9. Manager Bot subscription menu handler with 1 master (direct subscription card).
10. Manager Bot subscription menu handler with multiple masters (selection keyboard).
11. Subscription screen rendering for TRIAL status (remaining days, 499 ₽/mo, zero hardcoded prices).
12. Subscription screen rendering for ACTIVE status (paid_until date, tariff name, dynamic price).
13. Subscription screen rendering for EXPIRED status (paused booking warning, data preserved).
14. Subscription screen rendering for SUSPENDED status (admin lock, support links).
15. Dynamic button text in subscription_card_keyboard based on EffectiveSubscriptionStatus.
16. Strict IDOR protection on subscription payment creation (owner check).
17. Extension date calculation logic (ACTIVE -> paid_until+30d, TRIAL -> trial_ends_at+30d, EXPIRED -> now+30d).
18. Payment processing idempotency (duplicate webhook callbacks ignored, no double extension).
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
from app.database.models.master import Master, MasterSettings, MasterStatus, SubscriptionStatus
from app.database.models.subscription import (
    EffectiveSubscriptionStatus,
    PromoCode,
    SubscriptionPayment,
    SubscriptionPeriod,
    SubscriptionPlan,
)
from app.database.models.user import User
from app.manager_bot.handlers import (
    _show_subscription_screen,
    cb_subscription_menu,
    cb_subscription_screen,
)
from app.manager_bot.keyboards import (
    main_menu_keyboard,
    subscription_card_keyboard,
    subscription_projects_keyboard,
)
from app.services.exceptions import BillingIDORViolationError
from app.services.subscription_service import SubscriptionService
from tests.conftest import requires_postgres


def _next_telegram_id() -> int:
    return random.randint(2_000_000_000, 3_000_000_000)


async def _create_user(session: AsyncSession, first_name: str = "TestMaster") -> User:
    user = User(telegram_id=_next_telegram_id(), first_name=first_name)
    session.add(user)
    await session.flush()
    return user


async def _create_master(
    session: AsyncSession,
    owner_user_id: int,
    display_name: str = "Studio Flow",
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


# ---------------------------------------------------------------------------
# 1. Primary Tariff basic_monthly in DB
# ---------------------------------------------------------------------------
@requires_postgres
@pytest.mark.asyncio
async def test_basic_monthly_plan_in_db(pg_session: AsyncSession) -> None:
    stmt = select(SubscriptionPlan).where(SubscriptionPlan.code == "basic_monthly")
    res = await pg_session.execute(stmt)
    plan = res.scalars().first()

    assert plan is not None, "Primary plan 'basic_monthly' must exist in DB"
    assert plan.code == "basic_monthly"
    assert plan.name == "ZapisFlow Basic"
    assert plan.price == Decimal("499.00")
    assert plan.period_days == 30
    assert plan.is_active is True
    assert plan.sort_order == 1


# ---------------------------------------------------------------------------
# 2. SubscriptionPlan model aliases
# ---------------------------------------------------------------------------
def test_subscription_plan_model_aliases() -> None:
    plan = SubscriptionPlan(
        code="test_plan",
        name="Test Plan",
        price=Decimal("499.00"),
        period_days=30,
        sort_order=1,
    )
    assert plan.price_rub == Decimal("499.00")
    assert plan.duration_days == 30


# ---------------------------------------------------------------------------
# 3. get_active_plan() defaults to basic_monthly
# ---------------------------------------------------------------------------
@requires_postgres
@pytest.mark.asyncio
async def test_get_active_plan_defaults_to_basic_monthly(pg_session: AsyncSession) -> None:
    service = SubscriptionService(pg_session)
    plan = await service.get_active_plan()

    assert plan is not None
    assert plan.code == "basic_monthly"
    assert plan.price == Decimal("499.00")
    assert plan.is_active is True


# ---------------------------------------------------------------------------
# 4. list_active_plans() ordered by sort_order
# ---------------------------------------------------------------------------
@requires_postgres
@pytest.mark.asyncio
async def test_list_active_plans_ordered_by_sort_order(pg_session: AsyncSession) -> None:
    service = SubscriptionService(pg_session)
    plans = await service.list_active_plans()

    assert len(plans) >= 1
    # Check monotonic order of sort_order
    for i in range(len(plans) - 1):
        assert plans[i].sort_order <= plans[i + 1].sort_order
    # First active plan must be basic_monthly
    assert plans[0].code == "basic_monthly"
    assert plans[0].sort_order == 1


# ---------------------------------------------------------------------------
# 5. PromoCode ORM persistence and metadata
# ---------------------------------------------------------------------------
@requires_postgres
@pytest.mark.asyncio
async def test_promocode_model_persistence(pg_session: AsyncSession) -> None:
    unique_code = f"PILOT_{random.randint(1000, 9999)}"
    promo = PromoCode(
        code=unique_code,
        discount_type="PERCENT",
        discount_value=Decimal("20.00"),
        max_uses=50,
        used_count=0,
        valid_from=datetime.now(timezone.utc),
        valid_until=datetime.now(timezone.utc) + timedelta(days=90),
        is_active=True,
    )
    pg_session.add(promo)
    await pg_session.commit()

    # Query back
    stmt = select(PromoCode).where(PromoCode.code == unique_code)
    res = await pg_session.execute(stmt)
    loaded = res.scalar_one()

    assert loaded.id is not None
    assert loaded.code == unique_code
    assert loaded.discount_type == "PERCENT"
    assert loaded.discount_value == Decimal("20.00")
    assert loaded.max_uses == 50
    assert loaded.used_count == 0
    assert loaded.is_active is True


# ---------------------------------------------------------------------------
# 6. Support settings configuration
# ---------------------------------------------------------------------------
def test_support_settings_configuration() -> None:
    assert settings.support_telegram_username is not None
    assert settings.support_url == f"https://t.me/{settings.support_telegram_username}"
    assert settings.support_tag == f"@{settings.support_telegram_username}"


# ---------------------------------------------------------------------------
# 7. Manager Bot main menu keyboard has [💳 Подписка] and [🆘 Поддержка]
# ---------------------------------------------------------------------------
def test_main_menu_keyboard_has_subscription_and_support() -> None:
    kb = main_menu_keyboard()
    callbacks = [btn.callback_data for row in kb.inline_keyboard for btn in row if btn.callback_data]
    urls = [btn.url for row in kb.inline_keyboard for btn in row if btn.url]

    assert "mgr:sub:menu" in callbacks
    assert settings.support_url in urls


def _mock_callback(user: User, data: str = "mgr:sub:menu") -> MagicMock:
    cb = MagicMock()
    cb.data = data
    cb.from_user = MagicMock()
    cb.from_user.id = user.telegram_id
    cb.from_user.first_name = user.first_name
    cb.from_user.last_name = None
    cb.from_user.username = "testmaster"
    cb.message = MagicMock()
    cb.message.edit_text = AsyncMock()
    cb.answer = AsyncMock()
    return cb


# ---------------------------------------------------------------------------
# 8. Subscription menu handler with 0 masters
# ---------------------------------------------------------------------------
@requires_postgres
@pytest.mark.asyncio
async def test_subscription_menu_handler_zero_masters(pg_session: AsyncSession) -> None:
    user = await _create_user(pg_session, "NoProjectsUser")
    await pg_session.commit()

    cb = _mock_callback(user, "mgr:sub:menu")
    state = MagicMock()
    state.clear = AsyncMock()

    await cb_subscription_menu(cb, state, pg_session)

    cb.message.edit_text.assert_called_once()
    msg_text = cb.message.edit_text.call_args[0][0]
    assert "У вас пока нет созданных проектов" in msg_text
    assert "14 дней бесплатного пробного периода" in msg_text


# ---------------------------------------------------------------------------
# 9. Subscription menu handler with 1 master
# ---------------------------------------------------------------------------
@requires_postgres
@pytest.mark.asyncio
async def test_subscription_menu_handler_single_master(pg_session: AsyncSession) -> None:
    user = await _create_user(pg_session, "SoloMasterUser")
    master = await _create_master(
        pg_session,
        user.id,
        display_name="Solo Master Studio",
        subscription_status=SubscriptionStatus.TRIAL,
        trial_ends_at=datetime.now(timezone.utc) + timedelta(days=10),
    )
    await pg_session.commit()

    cb = _mock_callback(user, "mgr:sub:menu")
    state = MagicMock()
    state.clear = AsyncMock()

    await cb_subscription_menu(cb, state, pg_session)

    cb.message.edit_text.assert_called_once()
    msg_text = cb.message.edit_text.call_args[0][0]
    assert "Solo Master Studio" in msg_text
    assert "Пробный период" in msg_text
    assert "ZapisFlow Basic" in msg_text
    assert "499 ₽/мес" in msg_text


# ---------------------------------------------------------------------------
# 10. Subscription menu handler with multiple masters
# ---------------------------------------------------------------------------
@requires_postgres
@pytest.mark.asyncio
async def test_subscription_menu_handler_multiple_masters(pg_session: AsyncSession) -> None:
    user = await _create_user(pg_session, "MultiMasterUser")
    master1 = await _create_master(pg_session, user.id, display_name="Salon Alpha")
    master2 = await _create_master(pg_session, user.id, display_name="Salon Beta")
    await pg_session.commit()

    cb = _mock_callback(user, "mgr:sub:menu")
    state = MagicMock()
    state.clear = AsyncMock()

    await cb_subscription_menu(cb, state, pg_session)

    cb.message.edit_text.assert_called_once()
    msg_text = cb.message.edit_text.call_args[0][0]
    reply_markup = cb.message.edit_text.call_args[1]["reply_markup"]

    assert "Выберите проект" in msg_text
    button_callbacks = [
        btn.callback_data for row in reply_markup.inline_keyboard for btn in row if btn.callback_data
    ]
    assert f"mgr:sub:{master1.id}" in button_callbacks
    assert f"mgr:sub:{master2.id}" in button_callbacks


# ---------------------------------------------------------------------------
# 11. Subscription screen rendering for TRIAL status
# ---------------------------------------------------------------------------
@requires_postgres
@pytest.mark.asyncio
async def test_subscription_screen_trial_status(pg_session: AsyncSession) -> None:
    user = await _create_user(pg_session, "TrialOwner")
    trial_end = datetime.now(timezone.utc) + timedelta(days=7)
    master = await _create_master(
        pg_session,
        user.id,
        display_name="Trial Studio",
        subscription_status=SubscriptionStatus.TRIAL,
        trial_ends_at=trial_end,
    )
    await pg_session.commit()

    cb = MagicMock()
    cb.from_user = MagicMock()
    cb.from_user.id = user.telegram_id
    cb.message = MagicMock()
    cb.message.edit_text = AsyncMock()
    cb.answer = AsyncMock()

    await _show_subscription_screen(cb, master, pg_session)

    msg_text = cb.message.edit_text.call_args[0][0]
    markup = cb.message.edit_text.call_args[1]["reply_markup"]

    assert "Пробный период" in msg_text
    assert "ZapisFlow Basic" in msg_text
    assert "499 ₽/мес" in msg_text
    # Button check
    texts = [btn.text for row in markup.inline_keyboard for btn in row]
    assert any("Оформить: ZapisFlow Basic — 499 ₽" in t for t in texts)


# ---------------------------------------------------------------------------
# 12. Subscription screen rendering for ACTIVE status
# ---------------------------------------------------------------------------
@requires_postgres
@pytest.mark.asyncio
async def test_subscription_screen_active_status(pg_session: AsyncSession) -> None:
    user = await _create_user(pg_session, "ActiveOwner")
    paid_until = datetime.now(timezone.utc) + timedelta(days=20)
    master = await _create_master(
        pg_session,
        user.id,
        display_name="Active Studio",
        subscription_status=SubscriptionStatus.ACTIVE,
        paid_until=paid_until,
    )
    await pg_session.commit()

    cb = MagicMock()
    cb.from_user = MagicMock()
    cb.from_user.id = user.telegram_id
    cb.message = MagicMock()
    cb.message.edit_text = AsyncMock()
    cb.answer = AsyncMock()

    await _show_subscription_screen(cb, master, pg_session)

    msg_text = cb.message.edit_text.call_args[0][0]
    markup = cb.message.edit_text.call_args[1]["reply_markup"]

    assert "Подписка активна" in msg_text
    assert "ZapisFlow Basic" in msg_text
    assert paid_until.strftime("%d.%m.%Y") in msg_text
    # Button check
    texts = [btn.text for row in markup.inline_keyboard for btn in row]
    assert any("Продлить: ZapisFlow Basic — 499 ₽" in t for t in texts)


# ---------------------------------------------------------------------------
# 13. Subscription screen rendering for EXPIRED status
# ---------------------------------------------------------------------------
@requires_postgres
@pytest.mark.asyncio
async def test_subscription_screen_expired_status(pg_session: AsyncSession) -> None:
    user = await _create_user(pg_session, "ExpiredOwner")
    master = await _create_master(
        pg_session,
        user.id,
        display_name="Expired Studio",
        subscription_status=SubscriptionStatus.EXPIRED,
        trial_ends_at=datetime.now(timezone.utc) - timedelta(days=5),
    )
    await pg_session.commit()

    cb = MagicMock()
    cb.from_user = MagicMock()
    cb.from_user.id = user.telegram_id
    cb.message = MagicMock()
    cb.message.edit_text = AsyncMock()
    cb.answer = AsyncMock()

    await _show_subscription_screen(cb, master, pg_session)

    msg_text = cb.message.edit_text.call_args[0][0]
    markup = cb.message.edit_text.call_args[1]["reply_markup"]

    assert "Подписка истекла" in msg_text
    assert "приём новых записей клиентами временно приостановлен" in msg_text
    assert "сохранены" in msg_text
    # Button check
    texts = [btn.text for row in markup.inline_keyboard for btn in row]
    assert any("Оплатить 499 ₽ (ZapisFlow Basic)" in t for t in texts)


# ---------------------------------------------------------------------------
# 14. Subscription screen rendering for SUSPENDED status
# ---------------------------------------------------------------------------
@requires_postgres
@pytest.mark.asyncio
async def test_subscription_screen_suspended_status(pg_session: AsyncSession) -> None:
    user = await _create_user(pg_session, "SuspendedOwner")
    master = await _create_master(
        pg_session,
        user.id,
        display_name="Suspended Studio",
        subscription_status=SubscriptionStatus.SUSPENDED,
    )
    await pg_session.commit()

    cb = MagicMock()
    cb.from_user = MagicMock()
    cb.from_user.id = user.telegram_id
    cb.message = MagicMock()
    cb.message.edit_text = AsyncMock()
    cb.answer = AsyncMock()

    await _show_subscription_screen(cb, master, pg_session)

    msg_text = cb.message.edit_text.call_args[0][0]
    markup = cb.message.edit_text.call_args[1]["reply_markup"]

    assert "Подписка заблокирована" in msg_text
    assert settings.support_tag in msg_text
    urls = [btn.url for row in markup.inline_keyboard for btn in row if btn.url]
    assert settings.support_url in urls


# ---------------------------------------------------------------------------
# 15. Dynamic button text in subscription_card_keyboard
# ---------------------------------------------------------------------------
def test_dynamic_keyboard_button_labels_by_status() -> None:
    plan = SubscriptionPlan(
        code="basic_monthly",
        name="ZapisFlow Basic",
        price=Decimal("499.00"),
        period_days=30,
        sort_order=1,
    )

    kb_trial = subscription_card_keyboard(1, [plan], EffectiveSubscriptionStatus.TRIAL_ACTIVE)
    assert any("Оформить: ZapisFlow Basic — 499 ₽" in btn.text for row in kb_trial.inline_keyboard for btn in row)

    kb_active = subscription_card_keyboard(1, [plan], EffectiveSubscriptionStatus.PAID_ACTIVE)
    assert any("Продлить: ZapisFlow Basic — 499 ₽" in btn.text for row in kb_active.inline_keyboard for btn in row)

    kb_expired = subscription_card_keyboard(1, [plan], EffectiveSubscriptionStatus.EXPIRED)
    assert any("Оплатить 499 ₽ (ZapisFlow Basic)" in btn.text for row in kb_expired.inline_keyboard for btn in row)

    kb_suspended = subscription_card_keyboard(1, [plan], EffectiveSubscriptionStatus.SUSPENDED)
    assert any(btn.url == settings.support_url for row in kb_suspended.inline_keyboard for btn in row)


# ---------------------------------------------------------------------------
# 16. Strict IDOR protection on subscription payment creation
# ---------------------------------------------------------------------------
@requires_postgres
@pytest.mark.asyncio
async def test_idor_protection_create_payment(pg_session: AsyncSession) -> None:
    owner = await _create_user(pg_session, "RealOwner")
    attacker = await _create_user(pg_session, "Attacker")
    master = await _create_master(pg_session, owner.id)
    await pg_session.commit()

    service = SubscriptionService(pg_session)
    with pytest.raises(BillingIDORViolationError):
        await service.create_subscription_payment(
            master_id=master.id,
            actor_user_id=attacker.id,
            plan_code="basic_monthly",
        )


# ---------------------------------------------------------------------------
# 17. Extension date calculation logic
# ---------------------------------------------------------------------------
@requires_postgres
@pytest.mark.asyncio
async def test_subscription_renewal_calculation_active_trial_expired(pg_session: AsyncSession) -> None:
    service = SubscriptionService(pg_session)
    owner = await _create_user(pg_session, "CalcUser")

    # Case A: ACTIVE master extends from future paid_until
    now = datetime.now(timezone.utc)
    future_paid = now + timedelta(days=15)
    master_active = await _create_master(
        pg_session,
        owner.id,
        subscription_status=SubscriptionStatus.ACTIVE,
        paid_until=future_paid,
    )
    payment_a, intent_a = await service.create_subscription_payment(
        master_id=master_active.id,
        actor_user_id=owner.id,
        plan_code="basic_monthly",
    )
    await pg_session.commit()
    await service.process_successful_payment(payment_a.provider, payment_a.provider_payment_id)
    await pg_session.refresh(master_active)
    # Must be future_paid + 30 days (+/- a few seconds)
    expected_a = future_paid + timedelta(days=30)
    assert abs((master_active.paid_until - expected_a).total_seconds()) < 60

    # Case B: TRIAL master preserves remaining trial days
    trial_future = now + timedelta(days=10)
    master_trial = await _create_master(
        pg_session,
        owner.id,
        subscription_status=SubscriptionStatus.TRIAL,
        trial_ends_at=trial_future,
    )
    payment_b, intent_b = await service.create_subscription_payment(
        master_id=master_trial.id,
        actor_user_id=owner.id,
        plan_code="basic_monthly",
    )
    await pg_session.commit()
    await service.process_successful_payment(payment_b.provider, payment_b.provider_payment_id)
    await pg_session.refresh(master_trial)
    expected_b = trial_future + timedelta(days=30)
    assert abs((master_trial.paid_until - expected_b).total_seconds()) < 60

    # Case C: EXPIRED master extends from now
    master_expired = await _create_master(
        pg_session,
        owner.id,
        subscription_status=SubscriptionStatus.EXPIRED,
        trial_ends_at=now - timedelta(days=5),
    )
    payment_c, intent_c = await service.create_subscription_payment(
        master_id=master_expired.id,
        actor_user_id=owner.id,
        plan_code="basic_monthly",
    )
    await pg_session.commit()
    before_pay = datetime.now(timezone.utc)
    await service.process_successful_payment(payment_c.provider, payment_c.provider_payment_id)
    await pg_session.refresh(master_expired)
    expected_c = before_pay + timedelta(days=30)
    assert abs((master_expired.paid_until - expected_c).total_seconds()) < 60


# ---------------------------------------------------------------------------
# 18. Payment processing idempotency
# ---------------------------------------------------------------------------
@requires_postgres
@pytest.mark.asyncio
async def test_payment_idempotency(pg_session: AsyncSession) -> None:
    owner = await _create_user(pg_session, "IdempotentUser")
    master = await _create_master(
        pg_session,
        owner.id,
        subscription_status=SubscriptionStatus.EXPIRED,
        trial_ends_at=datetime.now(timezone.utc) - timedelta(days=2),
    )
    await pg_session.commit()

    service = SubscriptionService(pg_session)
    payment, _ = await service.create_subscription_payment(
        master_id=master.id,
        actor_user_id=owner.id,
        plan_code="basic_monthly",
    )
    await pg_session.commit()

    # First callback
    ok1 = await service.process_successful_payment(payment.provider, payment.provider_payment_id)
    assert ok1 is True
    await pg_session.commit()
    await pg_session.refresh(master)
    first_paid_until = master.paid_until

    # Second callback (duplicate webhook)
    ok2 = await service.process_successful_payment(payment.provider, payment.provider_payment_id)
    assert ok2 is True
    await pg_session.commit()
    await pg_session.refresh(master)

    # Must NOT have extended again
    assert master.paid_until == first_paid_until
