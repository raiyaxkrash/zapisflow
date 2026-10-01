"""Regression coverage for the Platform Admin YooKassa reconciliation callback."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

import pytest

from app.config.settings import settings
from app.database.models.master import Master
from app.database.models.subscription import SubscriptionPayment
from app.manager_bot.handlers import cb_admin_payment_check
from app.services.billing.yookassa_checkout import PaymentCheckResult
from app.services.exceptions import SubscriptionError


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["PENDING", "SUCCEEDED", "ALREADY_CONFIRMED", "CANCELLED", "GATEWAY_ERROR"])
async def test_platform_admin_check_uses_current_service_contract(status: str) -> None:
    callback = SimpleNamespace(
        data="mgr:admin:payment:check:42",
        answer=AsyncMock(),
        message=SimpleNamespace(edit_text=AsyncMock()),
    )
    payment = SimpleNamespace(id=42, provider="YOOKASSA", master_id=7)
    master = SimpleNamespace(id=7, owner_user_id=11)
    session = MagicMock()
    session.get = AsyncMock(side_effect=lambda model, key: {
        (SubscriptionPayment, 42): payment,
        (Master, 7): master,
    }.get((model, key)))
    result = PaymentCheckResult(
        status=status, payment_id=42, plan_name="Basic", plan_code="basic_monthly",
        period_days=30, amount=499, currency="RUB",
    )
    with (
        patch("app.manager_bot.handlers._ensure_platform_admin", new_callable=AsyncMock, return_value=(True, object())),
        patch.object(type(settings), "yookassa_credentials", new_callable=PropertyMock, return_value=("shop", "secret")),
        patch("app.manager_bot.handlers.YooKassaClient") as client_type,
        patch("app.manager_bot.handlers.YooKassaCheckoutService") as checkout_type,
        patch("app.manager_bot.handlers.PlatformAdminService") as admin_type,
    ):
        checkout_type.return_value.check_payment = AsyncMock(return_value=result)
        admin_type.return_value.get_payment_details = AsyncMock(return_value=None)
        await cb_admin_payment_check(callback, session)
        checkout_type.assert_called_once()
        assert checkout_type.call_args.args[1] is client_type.return_value
        checkout_type.return_value.check_payment.assert_awaited_once_with(
            payment_id=42, actor_user_id=11,
        )
        session.expire.assert_called_once_with(payment)
        assert status in callback.answer.await_args.args[0]


@pytest.mark.asyncio
@pytest.mark.parametrize("data", ["mgr:admin:payment:check:nope", "mgr:admin:payment:check:1x"])
async def test_platform_admin_check_rejects_invalid_callback(data: str) -> None:
    callback = SimpleNamespace(data=data, answer=AsyncMock())
    session = MagicMock()
    session.get = AsyncMock()
    with patch("app.manager_bot.handlers._ensure_platform_admin", new_callable=AsyncMock, return_value=(True, object())):
        await cb_admin_payment_check(callback, session)
    session.get.assert_not_awaited()
    callback.answer.assert_awaited_once()


@pytest.mark.asyncio
async def test_platform_admin_check_rejects_non_yookassa_payment() -> None:
    callback = SimpleNamespace(data="mgr:admin:payment:check:42", answer=AsyncMock())
    session = MagicMock()
    session.get = AsyncMock(return_value=SimpleNamespace(provider="OTHER"))
    with patch("app.manager_bot.handlers._ensure_platform_admin", new_callable=AsyncMock, return_value=(True, object())):
        await cb_admin_payment_check(callback, session)
    callback.answer.assert_awaited_once()
    assert "не найден" in callback.answer.await_args.args[0]


@pytest.mark.asyncio
async def test_platform_admin_check_hides_gateway_failure() -> None:
    callback = SimpleNamespace(data="mgr:admin:payment:check:42", answer=AsyncMock())
    session = MagicMock()
    session.get = AsyncMock(side_effect=lambda model, key: {
        (SubscriptionPayment, 42): SimpleNamespace(provider="YOOKASSA", master_id=7),
        (Master, 7): SimpleNamespace(owner_user_id=11),
    }.get((model, key)))
    with (
        patch("app.manager_bot.handlers._ensure_platform_admin", new_callable=AsyncMock, return_value=(True, object())),
        patch.object(type(settings), "yookassa_credentials", new_callable=PropertyMock, return_value=("shop", "secret")),
        patch("app.manager_bot.handlers.YooKassaClient"),
        patch("app.manager_bot.handlers.YooKassaCheckoutService") as checkout_type,
    ):
        checkout_type.return_value.check_payment = AsyncMock(side_effect=SubscriptionError("remote private detail"))
        await cb_admin_payment_check(callback, session)
    assert "remote private detail" not in callback.answer.await_args.args[0]
    assert "Не удалось проверить платёж" in callback.answer.await_args.args[0]
