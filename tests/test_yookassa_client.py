"""External checkout gateway contracts without contacting a real shop."""

import base64
from decimal import Decimal

import httpx
import pytest

from app.services.billing.yookassa_client import YooKassaClient, YooKassaGatewayError
from app.config.settings import settings
from app.database.models.subscription import EffectiveSubscriptionStatus, SubscriptionPlan
from app.manager_bot.keyboards import subscription_card_keyboard


@pytest.mark.asyncio
async def test_create_yookassa_payment_uses_snapshot_and_stable_idempotency_key() -> None:
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["request"] = request
        return httpx.Response(200, json={
            "id": "provider-123",
            "status": "pending",
            "paid": False,
            "amount": {"value": "499.00", "currency": "RUB"},
            "metadata": {"checkout_ref": "opaque-ref"},
            "confirmation": {"confirmation_url": "https://yoomoney.ru/checkout/123"},
        })

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        result = await YooKassaClient("shop", "secret", client=http).create_payment(
            checkout_ref="opaque-ref",
            amount=Decimal("499.00"),
            currency="RUB",
            description="ZapisFlow Basic, 30 days",
            return_url="https://pay.example.test/return",
        )

    request = seen["request"]
    assert request.method == "POST"
    assert request.headers["Idempotence-Key"] == "opaque-ref"
    assert request.headers["Authorization"] == "Basic " + base64.b64encode(b"shop:secret").decode()
    assert request.url == "https://api.yookassa.ru/v3/payments"
    body = __import__("json").loads(request.content)
    assert body["amount"] == {"value": "499.00", "currency": "RUB"}
    assert body["confirmation"] == {
        "type": "redirect", "return_url": "https://pay.example.test/return"
    }
    assert body["metadata"] == {"checkout_ref": "opaque-ref"}
    assert result.id == "provider-123"
    assert result.confirmation_url == "https://yoomoney.ru/checkout/123"


@pytest.mark.asyncio
async def test_yookassa_errors_do_not_include_credentials() -> None:
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(401, text="secret-token"))
    ) as http:
        with pytest.raises(YooKassaGatewayError) as caught:
            await YooKassaClient("shop", "secret-token", client=http).get_payment("provider-123")
    assert "secret-token" not in str(caught.value)


@pytest.mark.asyncio
async def test_yookassa_connect_retry_preserves_payment_idempotency_key() -> None:
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if len(requests) < 3:
            raise httpx.ConnectTimeout("temporary TLS timeout", request=request)
        return httpx.Response(200, json={
            "id": "provider-123", "status": "pending", "paid": False,
            "amount": {"value": "499.00", "currency": "RUB"},
            "metadata": {"checkout_ref": "opaque-ref"},
            "confirmation": {"confirmation_url": "https://yoomoney.ru/checkout/123"},
        })

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        payment = await YooKassaClient("shop", "secret", client=http).create_payment(
            checkout_ref="opaque-ref", amount=Decimal("499.00"), currency="RUB",
            description="ZapisFlow Basic", return_url="https://pay.zapisflow.su/billing/success",
        )
    assert payment.id == "provider-123"
    assert len(requests) == 3
    assert all(request.headers["Idempotence-Key"] == "opaque-ref" for request in requests)


@pytest.mark.asyncio
async def test_yookassa_rejects_unsafe_confirmation_url() -> None:
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(
        200, json={
            "id": "provider-123", "status": "pending", "paid": False,
            "amount": {"value": "499.00", "currency": "RUB"},
            "metadata": {"checkout_ref": "opaque-ref"},
            "confirmation": {"confirmation_url": "http://example.test/pay"},
        }
    ))) as http:
        with pytest.raises(YooKassaGatewayError, match="небезопасную"):
            await YooKassaClient("shop", "secret", client=http).get_payment("provider-123")


@pytest.mark.asyncio
async def test_yookassa_rejects_non_finite_amount_from_provider() -> None:
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(
        200, json={
            "id": "provider-123", "status": "succeeded", "paid": True,
            "amount": {"value": "NaN", "currency": "RUB"},
            "metadata": {"checkout_ref": "opaque-ref"},
        }
    ))) as http:
        with pytest.raises(YooKassaGatewayError, match="некорректные"):
            await YooKassaClient("shop", "secret", client=http).get_payment("provider-123")


@pytest.mark.asyncio
async def test_get_payment_rejects_path_injection() -> None:
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: pytest.fail("HTTP called"))) as http:
        with pytest.raises(ValueError):
            await YooKassaClient("shop", "secret", client=http).get_payment("../payments/another")


def test_manager_bot_uses_server_side_checkout_callback_without_price(monkeypatch) -> None:
    monkeypatch.setattr(settings, "payment_provider", "yookassa_web")
    plan = SubscriptionPlan(
        code="basic_monthly", name="ZapisFlow Basic", price=Decimal("499.00"),
        currency="RUB", period_days=30, is_active=True,
    )
    keyboard = subscription_card_keyboard(1, [plan], EffectiveSubscriptionStatus.EXPIRED)
    buttons = [button for row in keyboard.inline_keyboard for button in row]
    assert any(button.callback_data == "mgr:sub:pay:1:basic_monthly" for button in buttons)
    assert all("499" not in (button.callback_data or "") for button in buttons)
    assert not any(button.url and "checkout" in button.url for button in buttons)
