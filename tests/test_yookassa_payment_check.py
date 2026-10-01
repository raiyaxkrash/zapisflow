"""Comprehensive test suite for YooKassa manual payment check and verification flow.

Tests:
1. Succeeded payment check via YooKassa API -> activates subscription.
2. Pending payment check -> does not activate subscription, preserves pending state.
3. Canceled payment check -> marks payment cancelled, does not activate.
4. Gateway error (network/5xx) -> does not mark payment failed, preserves state for retry.
5. Already confirmed payment check -> returns already confirmed, no double extension (+0 days).
6. Amount mismatch protection -> rejects remote payment with mismatched amount.
7. Currency mismatch protection -> rejects remote payment with mismatched currency.
8. Metadata mismatch protection -> rejects remote payment with mismatched metadata.
9. IDOR protection -> user A cannot check payment of user B.
10. Concurrent race: webhook + manual check race -> exactly one extension.
11. Rate limiter: rapid consecutive clicks are throttled.
12. Lifecycle: TRIAL -> ACTIVE preserving remaining trial days.
13. Lifecycle: EXPIRED -> ACTIVE extending from now.
14. Lifecycle: ACTIVE -> renewal extending from existing paid_until.
15. Suspended project protection -> cannot check/activate payment for suspended master.
16. Manager Bot keyboard & UI: checkout keyboard has check button, dynamic texts formatted from Plan.
"""

import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Optional
from unittest.mock import AsyncMock, MagicMock, patch
import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config.settings import settings
from app.database.models.master import Master, MasterStatus, SubscriptionStatus
from app.database.models.subscription import (
    EffectiveSubscriptionStatus,
    SubscriptionPayment,
    SubscriptionPlan,
)
from app.database.models.user import User
from app.manager_bot.handlers import cb_subscription_check
from app.manager_bot.keyboards import (
    subscription_canceled_keyboard,
    subscription_checkout_keyboard,
    subscription_pending_keyboard,
    subscription_success_keyboard,
)
from app.services.billing.yookassa_checkout import (
    PROVIDER_CODE,
    PaymentCheckResult,
    YooKassaCheckoutService,
)
from app.services.billing.yookassa_client import YooKassaGatewayError, YooKassaPayment
from app.services.exceptions import BillingIDORViolationError, SubscriptionError
from app.services.rate_limiter import check_rate_limit
from tests.conftest import requires_postgres


pytestmark = [pytest.mark.asyncio, requires_postgres]


class FakeYooKassaClient:
    def __init__(self) -> None:
        self.payments: dict[str, YooKassaPayment] = {}
        self.get_calls: list[str] = []

    async def get_payment(self, payment_id: str) -> YooKassaPayment:
        self.get_calls.append(payment_id)
        if payment_id not in self.payments:
            raise YooKassaGatewayError("Payment not found in remote gateway")
        return self.payments[payment_id]

    def add_remote_payment(
        self,
        payment_id: str,
        *,
        status: str = "succeeded",
        paid: bool = True,
        amount: Decimal = Decimal("499.00"),
        currency: str = "RUB",
        checkout_ref: str = "",
        metadata: Optional[dict] = None,
        confirmation_url: Optional[str] = "https://yookassa.ru/checkout/test",
    ) -> YooKassaPayment:
        p = YooKassaPayment(
            id=payment_id,
            status=status,
            paid=paid,
            amount=amount,
            currency=currency,
            checkout_ref=checkout_ref,
            confirmation_url=confirmation_url,
            metadata=metadata or {},
        )
        self.payments[payment_id] = p
        return p


@pytest.fixture
def checkout_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "payment_provider", "yookassa_web")
    monkeypatch.setattr(settings, "payment_currency", "RUB")
    monkeypatch.setattr(settings, "billing_return_url", "https://pay.example.test/return")
    monkeypatch.setattr(settings, "yookassa_fiscal_mode", "self_employed")


async def _seed_data(
    pg_session: AsyncSession,
    *,
    sub_status: SubscriptionStatus = SubscriptionStatus.EXPIRED,
    trial_ends_at: Optional[datetime] = None,
    paid_until: Optional[datetime] = None,
    price: Decimal = Decimal("499.00"),
) -> tuple[User, Master, SubscriptionPlan, SubscriptionPayment]:
    uid = uuid.uuid4().hex[:12]
    user = User(telegram_id=int(uuid.uuid4().hex[:14], 16), first_name="Test Owner")
    pg_session.add(user)
    await pg_session.flush()

    master = Master(
        owner_user_id=user.id,
        display_name=f"Studio {uid}",
        status=MasterStatus.ACTIVE,
        subscription_status=sub_status,
        trial_ends_at=trial_ends_at,
        paid_until=paid_until,
        timezone="Europe/Moscow",
    )
    pg_session.add(master)
    await pg_session.flush()

    plan_stmt = select(SubscriptionPlan).where(SubscriptionPlan.code == "basic_monthly")
    res = await pg_session.execute(plan_stmt)
    plan = res.scalars().first()
    if plan is None:
        plan = SubscriptionPlan(
            code="basic_monthly",
            name="ZapisFlow Basic",
            price=price,
            period_days=30,
            sort_order=1,
            is_active=True,
        )
        pg_session.add(plan)
        await pg_session.flush()

    checkout_ref = uuid.uuid4()
    provider_payment_id = f"yk-{checkout_ref.hex[:16]}"
    payment = SubscriptionPayment(
        master_id=master.id,
        plan_id=plan.id,
        provider=PROVIDER_CODE,
        provider_payment_id=provider_payment_id,
        checkout_ref=checkout_ref,
        amount=price,
        currency="RUB",
        status="PENDING",
        period_days=plan.period_days,
    )
    pg_session.add(payment)
    await pg_session.commit()

    return user, master, plan, payment


# ---------------------------------------------------------------------------
# 1. Succeeded payment check
# ---------------------------------------------------------------------------
async def test_yookassa_check_payment_succeeded(
    pg_session: AsyncSession, checkout_config: None
) -> None:
    user, master, plan, payment = await _seed_data(pg_session)
    client = FakeYooKassaClient()
    client.add_remote_payment(
        payment.provider_payment_id,
        status="succeeded",
        paid=True,
        amount=payment.amount,
        currency="RUB",
        checkout_ref=str(payment.checkout_ref),
        metadata={
            "checkout_ref": str(payment.checkout_ref),
            "payment_id": str(payment.id),
            "user_id": str(user.id),
            "plan_id": str(plan.id),
        },
    )

    session_maker = async_sessionmaker(pg_session.bind, expire_on_commit=False)
    service = YooKassaCheckoutService(session_maker, client)

    result = await service.check_payment(payment_id=payment.id, actor_user_id=user.id)

    assert result.status == "SUCCEEDED"
    assert result.payment_id == payment.id
    assert result.plan_name == "ZapisFlow Basic"
    assert result.period_days == 30
    assert result.paid_until is not None

    await pg_session.refresh(payment)
    await pg_session.refresh(master)
    assert payment.status == "SUCCEEDED"
    assert master.subscription_status == SubscriptionStatus.ACTIVE
    assert master.paid_until is not None


# ---------------------------------------------------------------------------
# 2. Pending payment check
# ---------------------------------------------------------------------------
async def test_yookassa_check_payment_pending(
    pg_session: AsyncSession, checkout_config: None
) -> None:
    user, master, plan, payment = await _seed_data(pg_session)
    client = FakeYooKassaClient()
    client.add_remote_payment(
        payment.provider_payment_id,
        status="pending",
        paid=False,
        amount=payment.amount,
        currency="RUB",
        checkout_ref=str(payment.checkout_ref),
        metadata={
            "checkout_ref": str(payment.checkout_ref),
            "payment_id": str(payment.id),
            "user_id": str(user.id),
            "plan_id": str(plan.id),
        },
    )

    session_maker = async_sessionmaker(pg_session.bind, expire_on_commit=False)
    service = YooKassaCheckoutService(session_maker, client)

    result = await service.check_payment(payment_id=payment.id, actor_user_id=user.id)

    assert result.status == "PENDING"
    await pg_session.refresh(payment)
    await pg_session.refresh(master)
    assert payment.status == "PENDING"
    assert master.subscription_status == SubscriptionStatus.EXPIRED


# ---------------------------------------------------------------------------
# 3. Canceled payment check
# ---------------------------------------------------------------------------
async def test_yookassa_check_payment_canceled(
    pg_session: AsyncSession, checkout_config: None
) -> None:
    user, master, plan, payment = await _seed_data(pg_session)
    client = FakeYooKassaClient()
    client.add_remote_payment(
        payment.provider_payment_id,
        status="canceled",
        paid=False,
        amount=payment.amount,
        currency="RUB",
        checkout_ref=str(payment.checkout_ref),
        metadata={
            "checkout_ref": str(payment.checkout_ref),
            "payment_id": str(payment.id),
            "user_id": str(user.id),
            "plan_id": str(plan.id),
        },
    )

    session_maker = async_sessionmaker(pg_session.bind, expire_on_commit=False)
    service = YooKassaCheckoutService(session_maker, client)

    result = await service.check_payment(payment_id=payment.id, actor_user_id=user.id)

    assert result.status == "CANCELLED"
    await pg_session.refresh(payment)
    assert payment.status == "CANCELLED"


# ---------------------------------------------------------------------------
# 4. Gateway error (temporary network failure)
# ---------------------------------------------------------------------------
async def test_yookassa_check_payment_gateway_error(
    pg_session: AsyncSession, checkout_config: None
) -> None:
    user, master, plan, payment = await _seed_data(pg_session)
    # Empty client -> raises YooKassaGatewayError
    client = FakeYooKassaClient()

    session_maker = async_sessionmaker(pg_session.bind, expire_on_commit=False)
    service = YooKassaCheckoutService(session_maker, client)

    result = await service.check_payment(payment_id=payment.id, actor_user_id=user.id)

    assert result.status == "GATEWAY_ERROR"
    # Local payment remains PENDING for retry!
    await pg_session.refresh(payment)
    assert payment.status == "PENDING"


# ---------------------------------------------------------------------------
# 5. Already confirmed payment check (no double extension)
# ---------------------------------------------------------------------------
async def test_yookassa_check_payment_already_confirmed(
    pg_session: AsyncSession, checkout_config: None
) -> None:
    user, master, plan, payment = await _seed_data(pg_session)
    payment.status = "SUCCEEDED"
    initial_paid_until = datetime.now(timezone.utc) + timedelta(days=30)
    master.paid_until = initial_paid_until
    master.subscription_status = SubscriptionStatus.ACTIVE
    await pg_session.commit()

    client = FakeYooKassaClient()
    session_maker = async_sessionmaker(pg_session.bind, expire_on_commit=False)
    service = YooKassaCheckoutService(session_maker, client)

    result = await service.check_payment(payment_id=payment.id, actor_user_id=user.id)

    assert result.status == "ALREADY_CONFIRMED"
    await pg_session.refresh(master)
    assert master.paid_until == initial_paid_until


# ---------------------------------------------------------------------------
# 6. Amount mismatch protection
# ---------------------------------------------------------------------------
async def test_yookassa_check_payment_amount_mismatch(
    pg_session: AsyncSession, checkout_config: None
) -> None:
    user, master, plan, payment = await _seed_data(pg_session)
    client = FakeYooKassaClient()
    client.add_remote_payment(
        payment.provider_payment_id,
        status="succeeded",
        paid=True,
        amount=Decimal("100.00"),  # Mismatched amount!
        currency="RUB",
        checkout_ref=str(payment.checkout_ref),
        metadata={
            "checkout_ref": str(payment.checkout_ref),
            "payment_id": str(payment.id),
            "user_id": str(user.id),
            "plan_id": str(plan.id),
        },
    )

    session_maker = async_sessionmaker(pg_session.bind, expire_on_commit=False)
    service = YooKassaCheckoutService(session_maker, client)

    with pytest.raises(SubscriptionError, match="Параметры ЮKassa не совпадают"):
        await service.check_payment(payment_id=payment.id, actor_user_id=user.id)

    await pg_session.refresh(payment)
    assert payment.status == "PENDING"


# ---------------------------------------------------------------------------
# 7. Currency mismatch protection
# ---------------------------------------------------------------------------
async def test_yookassa_check_payment_currency_mismatch(
    pg_session: AsyncSession, checkout_config: None
) -> None:
    user, master, plan, payment = await _seed_data(pg_session)
    client = FakeYooKassaClient()
    client.add_remote_payment(
        payment.provider_payment_id,
        status="succeeded",
        paid=True,
        amount=payment.amount,
        currency="USD",  # Mismatched currency!
        checkout_ref=str(payment.checkout_ref),
        metadata={
            "checkout_ref": str(payment.checkout_ref),
            "payment_id": str(payment.id),
            "user_id": str(user.id),
            "plan_id": str(plan.id),
        },
    )

    session_maker = async_sessionmaker(pg_session.bind, expire_on_commit=False)
    service = YooKassaCheckoutService(session_maker, client)

    with pytest.raises(SubscriptionError, match="Параметры ЮKassa не совпадают"):
        await service.check_payment(payment_id=payment.id, actor_user_id=user.id)


# ---------------------------------------------------------------------------
# 8. Metadata mismatch protection
# ---------------------------------------------------------------------------
async def test_yookassa_check_payment_metadata_mismatch(
    pg_session: AsyncSession, checkout_config: None
) -> None:
    user, master, plan, payment = await _seed_data(pg_session)
    client = FakeYooKassaClient()
    client.add_remote_payment(
        payment.provider_payment_id,
        status="succeeded",
        paid=True,
        amount=payment.amount,
        currency="RUB",
        checkout_ref=str(payment.checkout_ref),
        metadata={
            "checkout_ref": str(payment.checkout_ref),
            "payment_id": str(payment.id),
            "user_id": "999999",  # Wrong user_id!
            "plan_id": str(plan.id),
        },
    )

    session_maker = async_sessionmaker(pg_session.bind, expire_on_commit=False)
    service = YooKassaCheckoutService(session_maker, client)

    with pytest.raises(SubscriptionError, match="Параметры ЮKassa не совпадают"):
        await service.check_payment(payment_id=payment.id, actor_user_id=user.id)


# ---------------------------------------------------------------------------
# 9. IDOR protection: User A cannot check User B payment
# ---------------------------------------------------------------------------
async def test_yookassa_check_payment_idor(
    pg_session: AsyncSession, checkout_config: None
) -> None:
    user, master, plan, payment = await _seed_data(pg_session)
    attacker = User(telegram_id=int(uuid.uuid4().hex[:14], 16), first_name="Attacker")
    pg_session.add(attacker)
    await pg_session.commit()

    client = FakeYooKassaClient()
    session_maker = async_sessionmaker(pg_session.bind, expire_on_commit=False)
    service = YooKassaCheckoutService(session_maker, client)

    with pytest.raises(BillingIDORViolationError):
        await service.check_payment(payment_id=payment.id, actor_user_id=attacker.id)


# ---------------------------------------------------------------------------
# 10. Concurrent race: Webhook + Check payment race
# ---------------------------------------------------------------------------
async def test_concurrent_webhook_and_manual_check_race(
    pg_session: AsyncSession, checkout_config: None
) -> None:
    user, master, plan, payment = await _seed_data(pg_session)
    client = FakeYooKassaClient()
    client.add_remote_payment(
        payment.provider_payment_id,
        status="succeeded",
        paid=True,
        amount=payment.amount,
        currency="RUB",
        checkout_ref=str(payment.checkout_ref),
        metadata={
            "checkout_ref": str(payment.checkout_ref),
            "payment_id": str(payment.id),
            "user_id": str(user.id),
            "plan_id": str(plan.id),
        },
    )

    session_maker = async_sessionmaker(pg_session.bind, expire_on_commit=False)
    service = YooKassaCheckoutService(session_maker, client)

    # Run reconcile (webhook simulation) and check_payment concurrently
    res_webhook, res_check = await asyncio.gather(
        service.reconcile(payment.provider_payment_id),
        service.check_payment(payment_id=payment.id, actor_user_id=user.id),
    )

    await pg_session.refresh(master)
    await pg_session.refresh(payment)

    assert payment.status == "SUCCEEDED"
    assert master.subscription_status == SubscriptionStatus.ACTIVE
    # Exactly one period added (+30 days), never double (+60 days)
    diff_days = (master.paid_until - datetime.now(timezone.utc)).total_seconds() / 86400
    assert 28 <= diff_days <= 31


# ---------------------------------------------------------------------------
# 11. Rate limiting check
# ---------------------------------------------------------------------------
async def test_rate_limiter_cooldown() -> None:
    key = f"rate_limit_test_{uuid.uuid4().hex}"
    # 1st call -> allowed
    ok1 = await check_rate_limit(None, key, cooldown_seconds=2)
    assert ok1 is True
    # Immediate 2nd call -> throttled
    ok2 = await check_rate_limit(None, key, cooldown_seconds=2)
    assert ok2 is False


# ---------------------------------------------------------------------------
# 12. Lifecycle: TRIAL -> ACTIVE preserving remaining trial days
# ---------------------------------------------------------------------------
async def test_lifecycle_trial_to_active(
    pg_session: AsyncSession, checkout_config: None
) -> None:
    now = datetime.now(timezone.utc)
    trial_future = now + timedelta(days=12)
    user, master, plan, payment = await _seed_data(
        pg_session,
        sub_status=SubscriptionStatus.TRIAL,
        trial_ends_at=trial_future,
    )
    client = FakeYooKassaClient()
    client.add_remote_payment(
        payment.provider_payment_id,
        status="succeeded",
        paid=True,
        amount=payment.amount,
        currency="RUB",
        checkout_ref=str(payment.checkout_ref),
        metadata={
            "checkout_ref": str(payment.checkout_ref),
            "payment_id": str(payment.id),
            "user_id": str(user.id),
            "plan_id": str(plan.id),
        },
    )

    session_maker = async_sessionmaker(pg_session.bind, expire_on_commit=False)
    service = YooKassaCheckoutService(session_maker, client)

    result = await service.check_payment(payment_id=payment.id, actor_user_id=user.id)
    assert result.status == "SUCCEEDED"

    await pg_session.refresh(master)
    assert master.subscription_status == SubscriptionStatus.ACTIVE
    # Remaining trial days (12) + 30 days = ~42 days from now
    diff_days = (master.paid_until - now).total_seconds() / 86400
    assert 40 <= diff_days <= 43


# ---------------------------------------------------------------------------
# 13. Lifecycle: EXPIRED -> ACTIVE extending from now
# ---------------------------------------------------------------------------
async def test_lifecycle_expired_to_active(
    pg_session: AsyncSession, checkout_config: None
) -> None:
    now = datetime.now(timezone.utc)
    user, master, plan, payment = await _seed_data(
        pg_session,
        sub_status=SubscriptionStatus.EXPIRED,
        trial_ends_at=now - timedelta(days=10),
    )
    client = FakeYooKassaClient()
    client.add_remote_payment(
        payment.provider_payment_id,
        status="succeeded",
        paid=True,
        amount=payment.amount,
        currency="RUB",
        checkout_ref=str(payment.checkout_ref),
        metadata={
            "checkout_ref": str(payment.checkout_ref),
            "payment_id": str(payment.id),
            "user_id": str(user.id),
            "plan_id": str(plan.id),
        },
    )

    session_maker = async_sessionmaker(pg_session.bind, expire_on_commit=False)
    service = YooKassaCheckoutService(session_maker, client)

    result = await service.check_payment(payment_id=payment.id, actor_user_id=user.id)
    assert result.status == "SUCCEEDED"

    await pg_session.refresh(master)
    assert master.subscription_status == SubscriptionStatus.ACTIVE
    diff_days = (master.paid_until - now).total_seconds() / 86400
    assert 29 <= diff_days <= 31


# ---------------------------------------------------------------------------
# 14. Lifecycle: ACTIVE -> renewal extending from existing paid_until
# ---------------------------------------------------------------------------
async def test_lifecycle_active_to_renewed(
    pg_session: AsyncSession, checkout_config: None
) -> None:
    now = datetime.now(timezone.utc)
    future_paid = now + timedelta(days=15)
    user, master, plan, payment = await _seed_data(
        pg_session,
        sub_status=SubscriptionStatus.ACTIVE,
        paid_until=future_paid,
    )
    client = FakeYooKassaClient()
    client.add_remote_payment(
        payment.provider_payment_id,
        status="succeeded",
        paid=True,
        amount=payment.amount,
        currency="RUB",
        checkout_ref=str(payment.checkout_ref),
        metadata={
            "checkout_ref": str(payment.checkout_ref),
            "payment_id": str(payment.id),
            "user_id": str(user.id),
            "plan_id": str(plan.id),
        },
    )

    session_maker = async_sessionmaker(pg_session.bind, expire_on_commit=False)
    service = YooKassaCheckoutService(session_maker, client)

    result = await service.check_payment(payment_id=payment.id, actor_user_id=user.id)
    assert result.status == "SUCCEEDED"

    await pg_session.refresh(master)
    # 15 days remaining + 30 days = ~45 days from now
    diff_days = (master.paid_until - now).total_seconds() / 86400
    assert 44 <= diff_days <= 46


# ---------------------------------------------------------------------------
# 15. Suspended master protection
# ---------------------------------------------------------------------------
async def test_suspended_master_cannot_check(
    pg_session: AsyncSession, checkout_config: None
) -> None:
    user, master, plan, payment = await _seed_data(
        pg_session, sub_status=SubscriptionStatus.SUSPENDED
    )
    client = FakeYooKassaClient()
    session_maker = async_sessionmaker(pg_session.bind, expire_on_commit=False)
    service = YooKassaCheckoutService(session_maker, client)

    with pytest.raises(SubscriptionError, match="заблокирована"):
        await service.check_payment(payment_id=payment.id, actor_user_id=user.id)


# ---------------------------------------------------------------------------
# 16. Keyboards and UI rendering
# ---------------------------------------------------------------------------
async def test_keyboards_structure() -> None:
    kb_checkout = subscription_checkout_keyboard(1, "https://pay.example", payment_id=123)
    cbs = [btn.callback_data for row in kb_checkout.inline_keyboard for btn in row if btn.callback_data]
    urls = [btn.url for row in kb_checkout.inline_keyboard for btn in row if btn.url]
    assert "mgr:sub:check:1:123" in cbs
    assert "https://pay.example" in urls

    kb_pending = subscription_pending_keyboard(1, 123)
    cbs_pending = [btn.callback_data for row in kb_pending.inline_keyboard for btn in row if btn.callback_data]
    assert "mgr:sub:check:1:123" in cbs_pending

    kb_canceled = subscription_canceled_keyboard(1, "basic_monthly")
    cbs_canceled = [btn.callback_data for row in kb_canceled.inline_keyboard for btn in row if btn.callback_data]
    assert "mgr:sub:pay:1:basic_monthly" in cbs_canceled

    kb_success = subscription_success_keyboard(1)
    cbs_success = [btn.callback_data for row in kb_success.inline_keyboard for btn in row if btn.callback_data]
    assert "mgr:sub:1" in cbs_success
    assert "mgr:menu" in cbs_success
