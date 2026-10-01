"""PostgreSQL regression tests for one-use external SaaS checkout links."""

import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import hashlib
from unittest.mock import AsyncMock, MagicMock, patch

from httpx import ASGITransport, AsyncClient
import pytest
from pydantic import SecretStr
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from app.config.settings import settings
from app.database.models.checkout_session import CheckoutSession
from app.database.models.master import Master, SubscriptionStatus
from app.database.models.subscription import SubscriptionPayment, SubscriptionPeriod, SubscriptionPlan
from app.database.models.user import User
from app.manager_bot.handlers import cb_subscription_pay
from app.services.billing.checkout_session import CheckoutSessionService
from app.services.billing.yookassa_checkout import YooKassaCheckoutService
from app.services.exceptions import BillingIDORViolationError, SubscriptionError
from app.web.app import create_app
from tests.conftest import requires_postgres
from tests.test_yookassa_checkout import FakeYooKassaClient, seed_project


pytestmark = [pytest.mark.asyncio, requires_postgres]


@pytest.fixture
def billing_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "payment_provider", "yookassa_web")
    monkeypatch.setattr(settings, "payment_currency", "RUB")
    monkeypatch.setattr(settings, "yookassa_mode", "test")
    monkeypatch.setattr(settings, "yookassa_test_shop_id", "test-shop")
    monkeypatch.setattr(settings, "yookassa_test_secret_key", SecretStr("test-secret"))
    monkeypatch.setattr(settings, "billing_return_url", "https://pay.zapisflow.su/billing/success")
    monkeypatch.setattr(settings, "yookassa_receipt_vat_code", "1")
    monkeypatch.setattr(settings, "yookassa_receipt_payment_subject", "service")
    monkeypatch.setattr(settings, "yookassa_receipt_payment_mode", "full_payment")


async def issue_link(sessions, *, owner_id: int, master_id: int, code: str, fake: FakeYooKassaClient):
    checkout = YooKassaCheckoutService(sessions, fake)
    capabilities = CheckoutSessionService(sessions, checkout)
    async with sessions.begin() as session:
        token, order = await capabilities.issue(
            session, actor_user_id=owner_id, master_id=master_id, plan_code=code,
        )
    return token, order, capabilities


async def test_checkout_token_is_random_hashed_tenant_bound_and_price_snapshotted(
    pg_engine: AsyncEngine, billing_config: None,
) -> None:
    sessions = async_sessionmaker(pg_engine, expire_on_commit=False)
    owner_a, master_a, code = await seed_project(sessions, price=Decimal("499.00"))
    owner_b, master_b, _ = await seed_project(sessions)
    fake = FakeYooKassaClient()

    token_a, order_a, capabilities = await issue_link(
        sessions, owner_id=owner_a, master_id=master_a, code=code, fake=fake,
    )
    token_b, _, _ = await issue_link(
        sessions, owner_id=owner_a, master_id=master_a, code=code, fake=fake,
    )
    assert token_a != token_b and len(token_a) == 43
    assert token_a != str(owner_a) and token_a != str(order_a.payment_id)
    assert order_a.amount == Decimal("499.00") and order_a.period_days == 30
    async with sessions() as session:
        row = await session.scalar(select(CheckoutSession).where(
            CheckoutSession.token_hash == hashlib.sha256(token_a.encode()).hexdigest()
        ))
        payment = await session.get(SubscriptionPayment, row.payment_id)
        assert row.user_id == owner_a and row.master_id == master_a
        assert row.payment_id == order_a.payment_id and row.plan_id == order_a.plan_id
        assert row.token_hash != token_a and row.expires_at > datetime.now(timezone.utc)
        assert payment.amount == Decimal("499.00") and payment.plan_id == row.plan_id

    with pytest.raises(BillingIDORViolationError):
        await issue_link(sessions, owner_id=owner_a, master_id=master_b, code=code, fake=fake)
    with pytest.raises(BillingIDORViolationError):
        await issue_link(sessions, owner_id=owner_b, master_id=master_a, code=code, fake=fake)
    assert (await capabilities.inspect(token_a)).user_id == owner_a


async def test_production_test_shop_only_allows_named_owner_and_revokes_existing_link(
    pg_engine: AsyncEngine, billing_config: None, monkeypatch: pytest.MonkeyPatch,
) -> None:
    sessions = async_sessionmaker(pg_engine, expire_on_commit=False)
    owner_a, master_a, code = await seed_project(sessions)
    owner_b, master_b, _ = await seed_project(sessions)
    async with sessions() as session:
        allowed_telegram_id = (await session.get(User, owner_a)).telegram_id
        denied_telegram_id = (await session.get(User, owner_b)).telegram_id

    monkeypatch.setattr(settings, "app_env", "production")
    monkeypatch.setattr(settings, "yookassa_allow_test_in_production", True)
    monkeypatch.setattr(settings, "yookassa_test_allowed_telegram_ids", [allowed_telegram_id])
    fake = FakeYooKassaClient()
    token, _, capabilities = await issue_link(
        sessions, owner_id=owner_a, master_id=master_a, code=code, fake=fake,
    )
    assert (await capabilities.inspect(token)).user_id == owner_a
    with pytest.raises(SubscriptionError, match="Тестовая оплата"):
        await issue_link(sessions, owner_id=owner_b, master_id=master_b, code=code, fake=fake)
    assert denied_telegram_id not in settings.yookassa_test_allowed_telegram_ids

    monkeypatch.setattr(settings, "yookassa_test_allowed_telegram_ids", [])
    with pytest.raises(SubscriptionError):
        await capabilities.inspect(token)
    with pytest.raises(SubscriptionError):
        await capabilities.consume(token, "owner@example.com")
    assert fake.create_calls == []


async def test_unknown_checkout_token_returns_404(
    pg_engine: AsyncEngine, billing_config: None,
) -> None:
    sessions = async_sessionmaker(pg_engine, expire_on_commit=False)
    app = create_app(session_factory=sessions)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://pay.zapisflow.su") as web:
        assert (await web.get("/billing/checkout/" + "A" * 43)).status_code == 404
        assert (await web.get("/billing/checkout/invalid")).status_code == 404
        assert (await web.post(
            "/billing/checkout/" + "A" * 43 + "/pay",
            data={"email": "owner@example.com"},
        )).status_code == 404


async def test_expired_used_and_inactive_checkout_are_rejected(
    pg_engine: AsyncEngine, billing_config: None,
) -> None:
    sessions = async_sessionmaker(pg_engine, expire_on_commit=False)
    owner, master, code = await seed_project(sessions)
    fake = FakeYooKassaClient()
    token, _, capabilities = await issue_link(sessions, owner_id=owner, master_id=master, code=code, fake=fake)
    async with sessions.begin() as session:
        row = await session.scalar(select(CheckoutSession).where(
            CheckoutSession.token_hash == hashlib.sha256(token.encode()).hexdigest()
        ))
        row.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    with pytest.raises(SubscriptionError):
        await capabilities.consume(token, "owner@example.com")

    token, order, _ = await issue_link(sessions, owner_id=owner, master_id=master, code=code, fake=fake)
    async with sessions.begin() as session:
        plan = await session.get(SubscriptionPlan, order.plan_id)
        plan.is_active = False
    with pytest.raises(SubscriptionError):
        await capabilities.inspect(token)
    with pytest.raises(SubscriptionError):
        await capabilities.consume(token, "owner@example.com")


async def test_concurrent_consumers_claim_once(
    pg_engine: AsyncEngine, billing_config: None,
) -> None:
    sessions = async_sessionmaker(pg_engine, expire_on_commit=False)
    owner, master, code = await seed_project(sessions)
    token, _, capabilities = await issue_link(
        sessions, owner_id=owner, master_id=master, code=code, fake=FakeYooKassaClient(),
    )
    results = await asyncio.gather(
        capabilities.consume(token, "one@example.com"),
        capabilities.consume(token, "two@example.com"),
        return_exceptions=True,
    )
    assert sum(isinstance(result, tuple) for result in results) == 1
    assert sum(isinstance(result, SubscriptionError) for result in results) == 1
    async with sessions() as session:
        row = await session.scalar(select(CheckoutSession).where(
            CheckoutSession.token_hash == hashlib.sha256(token.encode()).hexdigest()
        ))
        assert row.status == "USED" and row.used_at is not None
        assert row.receipt_email in {"one@example.com", "two@example.com"}


async def test_web_checkout_ignores_browser_price_plan_and_user_parameters(
    pg_engine: AsyncEngine, billing_config: None,
) -> None:
    sessions = async_sessionmaker(pg_engine, expire_on_commit=False)
    owner_a, master_a, code_a = await seed_project(sessions, price=Decimal("499.00"))
    owner_b, master_b, code_b = await seed_project(sessions, price=Decimal("999.00"))
    fake = FakeYooKassaClient()
    token, order, _ = await issue_link(sessions, owner_id=owner_a, master_id=master_a, code=code_a, fake=fake)
    app = create_app(session_factory=sessions)
    with patch("app.web.app.YooKassaClient", return_value=fake):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="https://pay.zapisflow.su") as web:
            page = await web.get(f"/billing/checkout/{token}")
            assert page.status_code == 200 and "499 ₽" in page.text and "30 дней" in page.text
            assert page.headers["cache-control"].startswith("no-store")
            assert page.headers["referrer-policy"] == "no-referrer"
            response = await web.post(
                f"/billing/checkout/{token}/pay",
                data={
                    "email": "OWNER@Example.com", "amount": "1", "price": "1",
                    "plan": code_b, "plan_id": str(order.plan_id + 100),
                    "user_id": str(owner_b), "master_id": str(master_b),
                    "payment_id": str(order.payment_id + 100),
                },
                follow_redirects=False,
            )
            assert response.status_code == 303
            assert response.headers["location"] == "https://yookassa.ru/checkout/test"
            assert (await web.post(
                f"/billing/checkout/{token}/pay", data={"email": "OWNER@Example.com"}
            )).status_code == 410
            assert (await web.get(f"/billing/checkout/{token}")).status_code == 410
            success = await web.get("/billing/success")
            assert success.status_code == 200 and "Проверяем оплату" in success.text
    assert len(fake.create_calls) == 1
    assert fake.create_calls[0]["amount"] == Decimal("499.00")
    assert fake.create_calls[0]["currency"] == "RUB"
    assert fake.create_calls[0]["receipt"]["customer"]["email"] == "owner@example.com"
    assert fake.create_calls[0]["receipt"]["items"][0]["amount"]["value"] == "499.00"
    async with sessions() as session:
        payment = await session.get(SubscriptionPayment, order.payment_id)
        assert payment.master_id == master_a and payment.amount == Decimal("499.00")
        assert payment.status == "PENDING"
        assert await session.scalar(select(func.count()).select_from(SubscriptionPeriod).where(
            SubscriptionPeriod.subscription_payment_id == payment.id
        )) == 0


async def test_plan_price_change_keeps_existing_checkout_payment_snapshot(
    pg_engine: AsyncEngine, billing_config: None,
) -> None:
    sessions = async_sessionmaker(pg_engine, expire_on_commit=False)
    owner, master, code = await seed_project(sessions, price=Decimal("499.00"))
    token, order, capabilities = await issue_link(
        sessions, owner_id=owner, master_id=master, code=code, fake=FakeYooKassaClient(),
    )
    async with sessions.begin() as session:
        plan = await session.get(SubscriptionPlan, order.plan_id)
        plan.price = Decimal("599.00")
    offer = await capabilities.inspect(token)
    assert offer.amount == Decimal("499.00")
    async with sessions() as session:
        assert (await session.get(SubscriptionPayment, order.payment_id)).amount == Decimal("499.00")


async def test_manager_pay_callback_creates_link_after_business_commit(
    pg_engine: AsyncEngine, billing_config: None, monkeypatch: pytest.MonkeyPatch,
) -> None:
    sessions = async_sessionmaker(pg_engine, expire_on_commit=False)
    owner_id, master_id, code = await seed_project(sessions)
    async with sessions() as session:
        owner = await session.get(User, owner_id)
        telegram_id = owner.telegram_id
    monkeypatch.setattr("app.manager_bot.handlers.async_session_factory", sessions)
    callback = MagicMock()
    callback.data = f"mgr:sub:pay:{master_id}:{code}"
    callback.from_user = MagicMock(id=telegram_id, first_name="Owner", last_name=None, username=None)
    callback.message = MagicMock()
    callback.message.edit_text = AsyncMock()
    callback.answer = AsyncMock()

    async with sessions() as session:
        await cb_subscription_pay(callback, session)
        assert not callback.message.edit_text.called
        assert len(session.info.get("post_commit", [])) == 1
        await session.commit()
        await session.info["post_commit"][0]()

    markup = callback.message.edit_text.call_args.kwargs["reply_markup"]
    buttons = [button for row in markup.inline_keyboard for button in row]
    urls = [button.url for button in buttons if button.url and "/billing/checkout/" in button.url]
    assert len(urls) == 1 and urls[0].startswith("https://pay.zapisflow.su/billing/checkout/")
    assert urls[0].split("/")[-1] != str(owner_id)
    async with sessions() as session:
        rows = (await session.scalars(select(CheckoutSession).where(CheckoutSession.master_id == master_id))).all()
        assert len(rows) == 1 and rows[0].user_id == owner_id


async def test_checkout_page_escapes_plan_name_and_uses_configured_support(
    pg_engine: AsyncEngine, billing_config: None, monkeypatch: pytest.MonkeyPatch,
) -> None:
    sessions = async_sessionmaker(pg_engine, expire_on_commit=False)
    owner, master, code = await seed_project(sessions)
    async with sessions.begin() as session:
        plan = await session.scalar(select(SubscriptionPlan).where(SubscriptionPlan.code == code))
        plan.name = "<script>alert(1)</script>"
    token, _, _ = await issue_link(
        sessions, owner_id=owner, master_id=master, code=code, fake=FakeYooKassaClient(),
    )
    monkeypatch.setattr(settings, "support_telegram_username", "help_zapisflow")
    app = create_app(session_factory=sessions)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://pay.zapisflow.su") as web:
        response = await web.get(f"/billing/checkout/{token}")
        assert response.status_code == 200
        assert "<script>" not in response.text
        assert "&lt;script&gt;" in response.text
        assert "@help_zapisflow" in response.text
        assert "https://t.me/help_zapisflow" in response.text
