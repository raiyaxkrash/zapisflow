"""PostgreSQL integration tests for independently authenticated YooKassa checkout."""

import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import uuid
from unittest.mock import patch

from httpx import ASGITransport, AsyncClient
import pytest
from pydantic import SecretStr
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from app.config.settings import settings
from app.database.models.master import Master, MasterStatus, SubscriptionStatus
from app.database.models.subscription import SubscriptionPayment, SubscriptionPeriod, SubscriptionPlan
from app.database.models.user import User
from app.services.billing.yookassa_checkout import YooKassaCheckoutService
from app.services.billing.yookassa_client import YooKassaGatewayError, YooKassaPayment
from app.services.exceptions import BillingIDORViolationError, PlanNotFoundError, SubscriptionError
from app.scheduler.jobs.yookassa_reconciliation import reconcile_pending_yookassa
from app.web.app import create_app
from tests.conftest import requires_postgres


pytestmark = [pytest.mark.asyncio, requires_postgres]


class FakeYooKassaClient:
    def __init__(self) -> None:
        self.payments: dict[str, YooKassaPayment] = {}
        self.create_calls: list[dict] = []
        self.get_calls: list[str] = []

    async def create_payment(self, **kwargs) -> YooKassaPayment:
        self.create_calls.append(kwargs)
        payment = YooKassaPayment(
            id=f"provider-{kwargs['checkout_ref']}",
            status="pending",
            paid=False,
            amount=kwargs["amount"],
            currency=kwargs["currency"],
            checkout_ref=kwargs["checkout_ref"],
            confirmation_url="https://yookassa.ru/checkout/test",
            metadata={
                "checkout_ref": kwargs["checkout_ref"],
                "payment_id": str(kwargs["payment_id"]),
                "user_id": str(kwargs["user_id"]),
                "plan_id": str(kwargs["plan_id"]),
                **{key: str(kwargs[key]) for key in
                   ("master_id", "actor_user_id", "plan_code") if kwargs.get(key) is not None},
            },
        )
        self.payments[payment.id] = payment
        return payment

    async def get_payment(self, payment_id: str) -> YooKassaPayment:
        self.get_calls.append(payment_id)
        try:
            return self.payments[payment_id]
        except KeyError as exc:
            raise YooKassaGatewayError("Payment unavailable in fake provider") from exc

    def replace(self, payment_id: str, **changes) -> None:
        old = self.payments[payment_id]
        self.payments[payment_id] = YooKassaPayment(**{
            key: changes.get(key, getattr(old, key))
            for key in YooKassaPayment.__dataclass_fields__
        })


@pytest.fixture
def checkout_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "payment_provider", "yookassa_web")
    monkeypatch.setattr(settings, "payment_currency", "RUB")
    monkeypatch.setattr(settings, "billing_return_url", "https://pay.example.test/return")
    monkeypatch.setattr(settings, "yookassa_fiscal_mode", "self_employed")


async def seed_project(
    sessions: async_sessionmaker,
    *,
    price: Decimal = Decimal("499.00"),
    active: bool = True,
    status: SubscriptionStatus = SubscriptionStatus.EXPIRED,
    paid_until: datetime | None = None,
) -> tuple[int, int, str]:
    unique = uuid.uuid4().hex
    async with sessions.begin() as session:
        owner = User(telegram_id=int(unique[:14], 16), first_name="Checkout owner")
        session.add(owner)
        await session.flush()
        master = Master(
            owner_user_id=owner.id,
            display_name="Checkout studio",
            status=MasterStatus.ACTIVE,
            subscription_status=status,
            paid_until=paid_until,
            timezone="Europe/Moscow",
        )
        plan = SubscriptionPlan(
            code=unique[:30], name="ZapisFlow Basic", price=price,
            currency="RUB", period_days=30, is_active=active, sort_order=1000,
        )
        session.add_all([master, plan])
        await session.flush()
        return owner.id, master.id, plan.code


async def payment_state(sessions: async_sessionmaker, checkout_ref: uuid.UUID):
    async with sessions() as session:
        payment = await session.scalar(select(SubscriptionPayment).where(
            SubscriptionPayment.checkout_ref == checkout_ref
        ))
        assert payment is not None
        return payment.id, payment.provider_payment_id, payment.status


async def test_direct_checkout_reuses_pending_order_without_email_or_checkout_session(
    pg_engine: AsyncEngine, checkout_config: None,
) -> None:
    sessions = async_sessionmaker(pg_engine, expire_on_commit=False)
    owner_id, master_id, code = await seed_project(sessions)
    fake = FakeYooKassaClient()
    checkout = YooKassaCheckoutService(sessions, fake)

    order, redirect = await checkout.open_direct_checkout(
        actor_user_id=owner_id, master_id=master_id, plan_code=code,
    )
    again, second = await checkout.open_direct_checkout(
        actor_user_id=owner_id, master_id=master_id, plan_code=code,
    )
    assert redirect.confirmation_url == second.confirmation_url == "https://yookassa.ru/checkout/test"
    assert order.payment_id == again.payment_id and order.checkout_ref == again.checkout_ref
    assert order.amount == Decimal("499.00") and order.currency == "RUB"
    assert len(fake.create_calls) == 1
    assert fake.create_calls[0]["receipt"] is None
    assert fake.create_calls[0]["amount"] == Decimal("499.00")
    assert fake.create_calls[0]["payment_id"] == order.payment_id
    assert fake.create_calls[0]["user_id"] == owner_id
    assert fake.create_calls[0]["plan_id"] == order.plan_id

    async with sessions() as session:
        rows = (await session.scalars(select(SubscriptionPayment).where(
            SubscriptionPayment.master_id == master_id
        ))).all()
        assert len(rows) == 1


async def test_direct_checkout_refuses_merchant_receipt_mode_without_email(
    pg_engine: AsyncEngine, checkout_config: None, monkeypatch: pytest.MonkeyPatch,
) -> None:
    sessions = async_sessionmaker(pg_engine, expire_on_commit=False)
    owner_id, master_id, code = await seed_project(sessions)
    monkeypatch.setattr(settings, "yookassa_fiscal_mode", "merchant_receipt")
    checkout = YooKassaCheckoutService(sessions, FakeYooKassaClient())
    with pytest.raises(SubscriptionError, match="email"):
        await checkout.open_direct_checkout(
            actor_user_id=owner_id, master_id=master_id, plan_code=code,
        )


async def test_concurrent_direct_checkout_uses_one_local_payment(
    pg_engine: AsyncEngine, checkout_config: None,
) -> None:
    sessions = async_sessionmaker(pg_engine, expire_on_commit=False)
    owner_id, master_id, code = await seed_project(sessions)
    fake = FakeYooKassaClient()
    checkout = YooKassaCheckoutService(sessions, fake)
    results = await asyncio.gather(*(
        checkout.open_direct_checkout(actor_user_id=owner_id, master_id=master_id, plan_code=code)
        for _ in range(2)
    ))
    assert results[0][0].payment_id == results[1][0].payment_id
    assert len({call["checkout_ref"] for call in fake.create_calls}) == 1
    async with sessions() as session:
        assert await session.scalar(select(func.count()).select_from(SubscriptionPayment).where(
            SubscriptionPayment.master_id == master_id
        )) == 1


async def test_canceled_provider_payment_allows_new_direct_order(
    pg_engine: AsyncEngine, checkout_config: None,
) -> None:
    sessions = async_sessionmaker(pg_engine, expire_on_commit=False)
    owner_id, master_id, code = await seed_project(sessions)
    fake = FakeYooKassaClient()
    checkout = YooKassaCheckoutService(sessions, fake)
    first, _ = await checkout.open_direct_checkout(
        actor_user_id=owner_id, master_id=master_id, plan_code=code,
    )
    fake.replace(f"provider-{first.checkout_ref}", status="canceled")
    second, redirect = await checkout.open_direct_checkout(
        actor_user_id=owner_id, master_id=master_id, plan_code=code,
    )
    assert second.payment_id != first.payment_id
    assert redirect.confirmation_url == "https://yookassa.ru/checkout/test"
    async with sessions() as session:
        assert (await session.get(SubscriptionPayment, first.payment_id)).status == "CANCELLED"
        assert (await session.get(SubscriptionPayment, second.payment_id)).status == "PENDING"


async def test_provider_metadata_mismatch_cannot_activate_direct_payment(
    pg_engine: AsyncEngine, checkout_config: None,
) -> None:
    sessions = async_sessionmaker(pg_engine, expire_on_commit=False)
    owner_id, master_id, code = await seed_project(sessions)
    fake = FakeYooKassaClient()
    checkout = YooKassaCheckoutService(sessions, fake)
    order, _ = await checkout.open_direct_checkout(
        actor_user_id=owner_id, master_id=master_id, plan_code=code,
    )
    provider_id = f"provider-{order.checkout_ref}"
    fake.replace(provider_id, status="succeeded", paid=True, metadata={
        "checkout_ref": str(order.checkout_ref),
        "payment_id": str(order.payment_id),
        "user_id": str(owner_id + 1),
        "plan_id": str(order.plan_id),
    })
    with pytest.raises(SubscriptionError, match="не совпадают"):
        await checkout.reconcile(provider_id)
    async with sessions() as session:
        assert (await session.get(SubscriptionPayment, order.payment_id)).status == "PENDING"
        assert await session.scalar(select(func.count()).select_from(SubscriptionPeriod).where(
            SubscriptionPeriod.subscription_payment_id == order.payment_id
        )) == 0


async def test_order_uses_database_price_snapshot_and_stable_provider_key(
    pg_engine: AsyncEngine, checkout_config: None,
) -> None:
    sessions = async_sessionmaker(pg_engine, expire_on_commit=False)
    owner_id, master_id, plan_code = await seed_project(sessions, price=Decimal("579.00"))
    client = FakeYooKassaClient()
    checkout = YooKassaCheckoutService(sessions, client)

    order = await checkout.create_order(
        actor_user_id=owner_id, master_id=master_id, plan_code=plan_code,
    )
    assert order.amount == Decimal("579.00")
    assert order.currency == "RUB"
    assert order.period_days == 30
    async with sessions.begin() as session:
        plan = await session.scalar(select(SubscriptionPlan).where(SubscriptionPlan.code == plan_code))
        plan.price = Decimal("699.00")

    redirect = await checkout.start_checkout(checkout_ref=order.checkout_ref, actor_user_id=owner_id)
    assert redirect.confirmation_url.startswith("https://")
    assert client.create_calls[0]["amount"] == Decimal("579.00")
    assert client.create_calls[0]["currency"] == "RUB"
    assert client.create_calls[0]["checkout_ref"] == str(order.checkout_ref)
    payment_id, provider_id, status = await payment_state(sessions, order.checkout_ref)
    assert payment_id > 0 and status == "PENDING"
    assert provider_id == f"provider-{order.checkout_ref}"
    await checkout.start_checkout(checkout_ref=order.checkout_ref, actor_user_id=owner_id)
    assert len(client.create_calls) == 1
    client.replace(provider_id, status="succeeded", paid=True)
    assert await checkout.reconcile(provider_id) is True
    async with sessions() as session:
        payment = await session.get(SubscriptionPayment, payment_id)
        assert payment.status == "SUCCEEDED"


async def test_owner_tenant_and_inactive_plan_are_rejected(
    pg_engine: AsyncEngine, checkout_config: None,
) -> None:
    sessions = async_sessionmaker(pg_engine, expire_on_commit=False)
    owner_a, master_a, plan_code = await seed_project(sessions)
    owner_b, master_b, inactive_code = await seed_project(sessions, active=False)
    client = FakeYooKassaClient()
    checkout = YooKassaCheckoutService(sessions, client)

    with pytest.raises(BillingIDORViolationError):
        await checkout.create_order(actor_user_id=owner_a, master_id=master_b, plan_code=plan_code)
    with pytest.raises(BillingIDORViolationError):
        await checkout.create_order(actor_user_id=owner_b, master_id=master_a, plan_code=plan_code)
    with pytest.raises(PlanNotFoundError):
        await checkout.create_order(actor_user_id=owner_b, master_id=master_b, plan_code=inactive_code)

    order = await checkout.create_order(
        actor_user_id=owner_a, master_id=master_a, plan_code=plan_code,
    )
    with pytest.raises(BillingIDORViolationError):
        await checkout.start_checkout(checkout_ref=order.checkout_ref, actor_user_id=owner_b)
    assert client.create_calls == []


async def test_suspension_during_provider_request_prevents_checkout_link(
    pg_engine: AsyncEngine, checkout_config: None,
) -> None:
    sessions = async_sessionmaker(pg_engine, expire_on_commit=False)
    owner_id, master_id, plan_code = await seed_project(sessions)
    client = FakeYooKassaClient()
    checkout = YooKassaCheckoutService(sessions, client)
    order = await checkout.create_order(
        actor_user_id=owner_id, master_id=master_id, plan_code=plan_code,
    )
    original_create = client.create_payment

    async def create_then_suspend(**kwargs) -> YooKassaPayment:
        remote = await original_create(**kwargs)
        async with sessions.begin() as session:
            master = await session.get(Master, master_id)
            master.subscription_status = SubscriptionStatus.SUSPENDED
        return remote

    client.create_payment = create_then_suspend
    with pytest.raises(SubscriptionError, match="заблокирована"):
        await checkout.start_checkout(checkout_ref=order.checkout_ref, actor_user_id=owner_id)
    async with sessions() as session:
        master = await session.get(Master, master_id)
        assert master.subscription_status == SubscriptionStatus.SUSPENDED


async def test_remote_amount_or_currency_mismatch_never_activates(
    pg_engine: AsyncEngine, checkout_config: None,
) -> None:
    sessions = async_sessionmaker(pg_engine, expire_on_commit=False)
    owner_id, master_id, plan_code = await seed_project(sessions)
    client = FakeYooKassaClient()
    checkout = YooKassaCheckoutService(sessions, client)
    order = await checkout.create_order(
        actor_user_id=owner_id, master_id=master_id, plan_code=plan_code,
    )
    await checkout.start_checkout(checkout_ref=order.checkout_ref, actor_user_id=owner_id)
    payment_id, provider_id, _ = await payment_state(sessions, order.checkout_ref)

    for mismatch in ({"amount": Decimal("1.00")}, {"currency": "USD"}):
        client.replace(provider_id, status="succeeded", paid=True, **mismatch)
        with pytest.raises(SubscriptionError, match="не совпадают"):
            await checkout.reconcile(provider_id)
        async with sessions() as session:
            payment = await session.get(SubscriptionPayment, payment_id)
            master = await session.get(Master, master_id)
            count = await session.scalar(select(func.count()).select_from(SubscriptionPeriod).where(
                SubscriptionPeriod.subscription_payment_id == payment_id
            ))
            assert payment.status == "PENDING"
            assert master.subscription_status == SubscriptionStatus.EXPIRED
            assert count == 0
        client.replace(provider_id, amount=Decimal("499.00"), currency="RUB")


async def test_succeeded_replay_and_concurrent_reconcile_create_one_period(
    pg_engine: AsyncEngine, checkout_config: None,
) -> None:
    sessions = async_sessionmaker(pg_engine, expire_on_commit=False)
    owner_id, master_id, plan_code = await seed_project(sessions)
    client = FakeYooKassaClient()
    checkout = YooKassaCheckoutService(sessions, client)
    order = await checkout.create_order(
        actor_user_id=owner_id, master_id=master_id, plan_code=plan_code,
    )
    await checkout.start_checkout(checkout_ref=order.checkout_ref, actor_user_id=owner_id)
    payment_id, provider_id, _ = await payment_state(sessions, order.checkout_ref)
    client.replace(provider_id, status="succeeded", paid=True)

    assert await asyncio.gather(checkout.reconcile(provider_id), checkout.reconcile(provider_id)) == [True, True]
    async with sessions() as session:
        payment = await session.get(SubscriptionPayment, payment_id)
        master = await session.get(Master, master_id)
        periods = (await session.scalars(select(SubscriptionPeriod).where(
            SubscriptionPeriod.subscription_payment_id == payment_id
        ))).all()
        assert payment.status == "SUCCEEDED"
        assert master.subscription_status == SubscriptionStatus.ACTIVE
        assert len(periods) == 1
        assert periods[0].ends_at == master.paid_until
        paid_until = master.paid_until
        assert timedelta(days=29, hours=23) < paid_until - datetime.now(timezone.utc) < timedelta(days=30, minutes=1)

    assert await checkout.reconcile(provider_id) is True
    async with sessions() as session:
        master = await session.get(Master, master_id)
        count = await session.scalar(select(func.count()).select_from(SubscriptionPeriod).where(
            SubscriptionPeriod.subscription_payment_id == payment_id
        ))
        assert master.paid_until == paid_until
        assert count == 1


async def test_active_subscription_extends_from_paid_until_and_suspension_stays(
    pg_engine: AsyncEngine, checkout_config: None,
) -> None:
    sessions = async_sessionmaker(pg_engine, expire_on_commit=False)
    original_until = datetime.now(timezone.utc) + timedelta(days=10)
    owner_id, master_id, plan_code = await seed_project(
        sessions, status=SubscriptionStatus.ACTIVE, paid_until=original_until,
    )
    client = FakeYooKassaClient()
    checkout = YooKassaCheckoutService(sessions, client)
    order = await checkout.create_order(
        actor_user_id=owner_id, master_id=master_id, plan_code=plan_code,
    )
    await checkout.start_checkout(checkout_ref=order.checkout_ref, actor_user_id=owner_id)
    payment_id, provider_id, _ = await payment_state(sessions, order.checkout_ref)
    client.replace(provider_id, status="succeeded", paid=True)
    assert await checkout.reconcile(provider_id) is True
    async with sessions() as session:
        master = await session.get(Master, master_id)
        assert master.paid_until == original_until + timedelta(days=30)

    suspended_owner, suspended_master, code = await seed_project(sessions)
    suspended_order = await checkout.create_order(
        actor_user_id=suspended_owner, master_id=suspended_master, plan_code=code,
    )
    await checkout.start_checkout(
        checkout_ref=suspended_order.checkout_ref, actor_user_id=suspended_owner,
    )
    suspended_payment_id, suspended_provider_id, _ = await payment_state(
        sessions, suspended_order.checkout_ref,
    )
    async with sessions.begin() as session:
        master = await session.get(Master, suspended_master)
        master.subscription_status = SubscriptionStatus.SUSPENDED
    client.replace(suspended_provider_id, status="succeeded", paid=True)
    assert await checkout.reconcile(suspended_provider_id) is True
    async with sessions() as session:
        master = await session.get(Master, suspended_master)
        payment = await session.get(SubscriptionPayment, suspended_payment_id)
        count = await session.scalar(select(func.count()).select_from(SubscriptionPeriod).where(
            SubscriptionPeriod.subscription_payment_id == suspended_payment_id
        ))
        assert master.subscription_status == SubscriptionStatus.SUSPENDED
        assert master.paid_until is not None
        assert payment.status == "SUCCEEDED"
        assert count == 1

    with pytest.raises(SubscriptionError, match="заблокирована"):
        await checkout.create_order(
            actor_user_id=suspended_owner, master_id=suspended_master, plan_code=code,
        )


async def test_yookassa_webhook_uses_verified_provider_state_and_replay_is_safe(
    pg_engine: AsyncEngine, checkout_config: None, monkeypatch: pytest.MonkeyPatch,
) -> None:
    sessions = async_sessionmaker(pg_engine, expire_on_commit=False)
    owner_id, master_id, plan_code = await seed_project(sessions)
    fake = FakeYooKassaClient()
    checkout = YooKassaCheckoutService(sessions, fake)
    order = await checkout.create_order(
        actor_user_id=owner_id, master_id=master_id, plan_code=plan_code,
    )
    await checkout.start_checkout(checkout_ref=order.checkout_ref, actor_user_id=owner_id)
    payment_id, provider_id, _ = await payment_state(sessions, order.checkout_ref)
    monkeypatch.setattr(settings, "yookassa_test_shop_id", "test-shop")
    monkeypatch.setattr(settings, "yookassa_test_secret_key", SecretStr("test-secret"))
    monkeypatch.setattr(settings, "yookassa_mode", "test")
    app = create_app(session_factory=sessions)
    payload = {
        "event": "payment.succeeded",
        "object": {"id": provider_id, "metadata": {"checkout_ref": str(order.checkout_ref)}},
    }
    with patch("app.web.app.YooKassaClient", return_value=fake):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as web:
            # The notification's event label cannot activate a still-pending
            # remote payment.
            assert (await web.post("/billing/yookassa/webhook", json=payload)).status_code == 200
            async with sessions() as session:
                assert (await session.get(SubscriptionPayment, payment_id)).status == "PENDING"
            fake.replace(provider_id, status="succeeded", paid=True)
            assert (await web.post("/billing/yookassa/webhook", json=payload)).status_code == 200
            assert (await web.post("/billing/yookassa/webhook", json=payload)).status_code == 200

    async with sessions() as session:
        payment = await session.get(SubscriptionPayment, payment_id)
        master = await session.get(Master, master_id)
        periods = (await session.scalars(select(SubscriptionPeriod).where(
            SubscriptionPeriod.subscription_payment_id == payment_id
        ))).all()
        assert payment.status == "SUCCEEDED"
        assert master.subscription_status == SubscriptionStatus.ACTIVE
        assert len(periods) == 1


async def test_return_url_never_activates_payment(
    pg_engine: AsyncEngine, checkout_config: None,
) -> None:
    sessions = async_sessionmaker(pg_engine, expire_on_commit=False)
    owner_id, master_id, code = await seed_project(sessions)
    fake = FakeYooKassaClient()
    checkout = YooKassaCheckoutService(sessions, fake)
    order, _ = await checkout.open_direct_checkout(
        actor_user_id=owner_id, master_id=master_id, plan_code=code,
    )
    app = create_app(session_factory=sessions)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://api.zapisflow.su") as web:
        response = await web.get("/billing/yookassa/return")
        assert response.status_code == 200
        assert "Проверяем оплату" in response.text
    async with sessions() as session:
        assert (await session.get(SubscriptionPayment, order.payment_id)).status == "PENDING"
        assert (await session.get(Master, master_id)).subscription_status == SubscriptionStatus.EXPIRED


async def test_yookassa_webhook_unknown_checkout_ref_does_not_query_provider(
    pg_engine: AsyncEngine, checkout_config: None, monkeypatch: pytest.MonkeyPatch,
) -> None:
    sessions = async_sessionmaker(pg_engine, expire_on_commit=False)
    monkeypatch.setattr(settings, "yookassa_test_shop_id", "test-shop")
    monkeypatch.setattr(settings, "yookassa_test_secret_key", SecretStr("test-secret"))
    app = create_app(session_factory=sessions)
    with patch("app.web.app.YooKassaClient") as factory:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as web:
            response = await web.post("/billing/yookassa/webhook", json={
                "event": "payment.succeeded",
                "object": {"id": "fake-id", "metadata": {"checkout_ref": str(uuid.uuid4())}},
            })
        factory.assert_not_called()
    assert response.status_code == 200


async def test_yookassa_webhook_retries_when_provider_verification_is_unavailable(
    pg_engine: AsyncEngine, checkout_config: None, monkeypatch: pytest.MonkeyPatch,
) -> None:
    sessions = async_sessionmaker(pg_engine, expire_on_commit=False)
    owner_id, master_id, plan_code = await seed_project(sessions)
    fake = FakeYooKassaClient()
    checkout = YooKassaCheckoutService(sessions, fake)
    order = await checkout.create_order(
        actor_user_id=owner_id, master_id=master_id, plan_code=plan_code,
    )
    await checkout.start_checkout(checkout_ref=order.checkout_ref, actor_user_id=owner_id)
    payment_id, provider_id, _ = await payment_state(sessions, order.checkout_ref)

    async def unavailable(_payment_id: str) -> YooKassaPayment:
        raise YooKassaGatewayError("provider unavailable")

    fake.get_payment = unavailable
    monkeypatch.setattr(settings, "yookassa_test_shop_id", "test-shop")
    monkeypatch.setattr(settings, "yookassa_test_secret_key", SecretStr("test-secret"))
    app = create_app(session_factory=sessions)
    with patch("app.web.app.YooKassaClient", return_value=fake):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as web:
            response = await web.post("/billing/yookassa/webhook", json={
                "event": "payment.succeeded",
                "object": {"id": provider_id, "metadata": {"checkout_ref": str(order.checkout_ref)}},
            })
    assert response.status_code == 503
    async with sessions() as session:
        assert (await session.get(SubscriptionPayment, payment_id)).status == "PENDING"


async def test_uncertain_old_checkout_is_not_recreated_after_provider_key_expiry(
    pg_engine: AsyncEngine, checkout_config: None,
) -> None:
    sessions = async_sessionmaker(pg_engine, expire_on_commit=False)
    owner_id, master_id, plan_code = await seed_project(sessions)
    fake = FakeYooKassaClient()
    checkout = YooKassaCheckoutService(sessions, fake)
    order = await checkout.create_order(
        actor_user_id=owner_id, master_id=master_id, plan_code=plan_code,
    )
    async with sessions.begin() as session:
        payment = await session.scalar(select(SubscriptionPayment).where(
            SubscriptionPayment.checkout_ref == order.checkout_ref
        ))
        payment.created_at = datetime.now(timezone.utc) - timedelta(hours=25)
    with pytest.raises(SubscriptionError, match="истекло"):
        await checkout.start_checkout(checkout_ref=order.checkout_ref, actor_user_id=owner_id)
    assert fake.create_calls == []


async def test_reconciliation_recovers_payment_without_webhook(
    pg_engine: AsyncEngine, checkout_config: None,
) -> None:
    sessions = async_sessionmaker(pg_engine, expire_on_commit=False)
    owner_id, master_id, plan_code = await seed_project(sessions)
    fake = FakeYooKassaClient()
    checkout = YooKassaCheckoutService(sessions, fake)
    order = await checkout.create_order(
        actor_user_id=owner_id, master_id=master_id, plan_code=plan_code,
    )
    await checkout.start_checkout(checkout_ref=order.checkout_ref, actor_user_id=owner_id)
    payment_id, provider_id, _ = await payment_state(sessions, order.checkout_ref)
    fake.replace(provider_id, status="succeeded", paid=True)

    assert await reconcile_pending_yookassa(session_maker=sessions, client=fake) == 1
    assert await reconcile_pending_yookassa(session_maker=sessions, client=fake) == 0
    async with sessions() as session:
        payment = await session.get(SubscriptionPayment, payment_id)
        count = await session.scalar(select(func.count()).select_from(SubscriptionPeriod).where(
            SubscriptionPeriod.subscription_payment_id == payment_id
        ))
        assert payment.status == "SUCCEEDED"
        assert count == 1


async def test_two_reconciliation_workers_claim_one_payment_once(
    pg_engine: AsyncEngine, checkout_config: None,
) -> None:
    sessions = async_sessionmaker(pg_engine, expire_on_commit=False)
    owner_id, master_id, plan_code = await seed_project(sessions)
    fake = FakeYooKassaClient()
    checkout = YooKassaCheckoutService(sessions, fake)
    order = await checkout.create_order(
        actor_user_id=owner_id, master_id=master_id, plan_code=plan_code,
    )
    await checkout.start_checkout(checkout_ref=order.checkout_ref, actor_user_id=owner_id)
    payment_id, provider_id, _ = await payment_state(sessions, order.checkout_ref)
    fake.replace(provider_id, status="succeeded", paid=True)
    fake.get_calls.clear()

    results = await asyncio.gather(
        reconcile_pending_yookassa(session_maker=sessions, batch_size=1000, client=fake),
        reconcile_pending_yookassa(session_maker=sessions, batch_size=1000, client=fake),
    )
    assert sum(results) == 1
    assert fake.get_calls.count(provider_id) == 1
    async with sessions() as session:
        count = await session.scalar(select(func.count()).select_from(SubscriptionPeriod).where(
            SubscriptionPeriod.subscription_payment_id == payment_id
        ))
        assert count == 1


async def test_canonical_provider_stages_order_without_external_call(pg_engine, checkout_config, monkeypatch):
    from app.services.subscription_service import SubscriptionService
    monkeypatch.setattr(settings, "payment_provider", "yookassa")
    monkeypatch.setattr(settings, "yookassa_test_shop_id", "test-shop")
    monkeypatch.setattr(settings, "yookassa_test_secret_key", SecretStr("test-only-secret"))
    sessions = async_sessionmaker(pg_engine, expire_on_commit=False)
    owner, master, plan_code = await seed_project(sessions)
    async with sessions.begin() as session:
        with patch("app.services.billing.yookassa_client.YooKassaClient.create_payment") as post:
            payment, intent = await SubscriptionService(session).create_subscription_payment(master, owner, plan_code)
            assert payment.status == "PENDING"
            assert intent.payment_url is None
            assert intent.provider_payment_id.startswith("checkout:")
            post.assert_not_called()
            ref = payment.checkout_ref
    client = FakeYooKassaClient()
    redirect = await YooKassaCheckoutService(sessions, client).start_checkout(checkout_ref=ref, actor_user_id=owner)
    assert redirect.confirmation_url
    assert client.create_calls[0]["master_id"] == master
    assert client.create_calls[0]["actor_user_id"] == owner
    assert client.create_calls[0]["plan_code"] == plan_code


@pytest.mark.parametrize("key", ["master_id", "actor_user_id", "plan_code"])
async def test_new_order_metadata_forgery_rejected(pg_engine, checkout_config, key):
    sessions = async_sessionmaker(pg_engine, expire_on_commit=False)
    owner, master, code = await seed_project(sessions)
    client = FakeYooKassaClient()
    service = YooKassaCheckoutService(sessions, client)
    order = await service.create_order(actor_user_id=owner, master_id=master, plan_code=code)
    await service.start_checkout(checkout_ref=order.checkout_ref, actor_user_id=owner)
    remote = next(iter(client.payments.values()))
    client.replace(remote.id, status="succeeded", paid=True, metadata={**remote.metadata, key: "forged"})
    with pytest.raises(SubscriptionError, match="не совпадают"):
        await service.reconcile(remote.id)
    async with sessions() as session:
        assert await session.scalar(select(func.count()).select_from(SubscriptionPeriod).where(
            SubscriptionPeriod.subscription_payment_id == order.payment_id)) == 0


async def test_merchant_receipt_direct_checkout_uses_validated_email_and_snapshot(pg_engine, checkout_config, monkeypatch):
    monkeypatch.setattr(settings, "yookassa_fiscal_mode", "merchant_receipt")
    monkeypatch.setattr(settings, "yookassa_receipt_vat_code", "1")
    monkeypatch.setattr(settings, "yookassa_receipt_payment_subject", "service")
    monkeypatch.setattr(settings, "yookassa_receipt_payment_mode", "full_prepayment")
    sessions = async_sessionmaker(pg_engine, expire_on_commit=False)
    owner, master, code = await seed_project(sessions)
    client = FakeYooKassaClient()
    service = YooKassaCheckoutService(sessions, client)
    with pytest.raises(SubscriptionError, match="email"):
        await service.open_direct_checkout(actor_user_id=owner, master_id=master, plan_code=code)
    order, redirect = await service.open_direct_checkout(
        actor_user_id=owner, master_id=master, plan_code=code, receipt_email="Buyer@example.test")
    assert redirect.confirmation_url
    receipt = client.create_calls[0]["receipt"]
    assert receipt["customer"]["email"] == "buyer@example.test"
    assert receipt["items"][0]["amount"]["value"] == "499.00"
    assert receipt["items"][0]["payment_subject"] == "service"
    assert receipt["items"][0]["payment_mode"] == "full_prepayment"


async def test_waiting_for_capture_never_activates_subscription(pg_engine, checkout_config):
    sessions = async_sessionmaker(pg_engine, expire_on_commit=False)
    owner, master, code = await seed_project(sessions)
    client = FakeYooKassaClient()
    service = YooKassaCheckoutService(sessions, client)
    order = await service.create_order(actor_user_id=owner, master_id=master, plan_code=code)
    await service.start_checkout(checkout_ref=order.checkout_ref, actor_user_id=owner)
    remote = next(iter(client.payments.values()))
    client.replace(remote.id, status="waiting_for_capture", confirmation_url=None)
    redirect = await service.start_checkout(checkout_ref=order.checkout_ref, actor_user_id=owner)
    assert redirect.status == "WAITING_FOR_CAPTURE"
    assert await service.reconcile(remote.id) is False
    async with sessions() as session:
        payment = await session.get(SubscriptionPayment, order.payment_id)
        assert payment.status == "PENDING"


async def test_uncertain_post_retries_with_immutable_receipt_and_request(pg_engine, checkout_config, monkeypatch):
    sessions = async_sessionmaker(pg_engine, expire_on_commit=False)
    owner, master, code = await seed_project(sessions)
    client = FakeYooKassaClient()
    service = YooKassaCheckoutService(sessions, client)
    order = await service.create_order(actor_user_id=owner, master_id=master, plan_code=code)
    real_create = client.create_payment
    requests = []
    async def uncertain(**kwargs):
        requests.append(kwargs)
        if len(requests) == 1:
            raise YooKassaGatewayError("Uncertain response")
        return await real_create(**kwargs)
    client.create_payment = uncertain
    receipt = {"customer": {"email": "original@example.test"}, "items": []}
    with pytest.raises(YooKassaGatewayError):
        await service.start_checkout(checkout_ref=order.checkout_ref, actor_user_id=owner, receipt=receipt)
    monkeypatch.setattr(settings, "billing_return_url", "https://new.example.test/return")
    async with sessions.begin() as session:
        plan = await session.get(SubscriptionPlan, order.plan_id)
        plan.name = "Renamed after uncertain POST"
    await service.start_checkout(checkout_ref=order.checkout_ref, actor_user_id=owner,
                                 receipt={"customer": {"email": "changed@example.test"}})
    assert requests[0] == requests[1]
