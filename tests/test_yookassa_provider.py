"""Provider contract and canonical production configuration regression tests."""
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from pydantic import SecretStr
from app.config.settings import Settings
from app.services.billing.yookassa_client import YooKassaClient, YooKassaGatewayError
from app.services.billing.yookassa_provider import YooKassaProvider


def config(**changes):
    values = {"PAYMENT_PROVIDER": "yookassa", "YOOKASSA_MODE": "live",
              "YOOKASSA_SHOP_ID": "123456", "YOOKASSA_SECRET_KEY": "not-a-real-secret",
              "BILLING_RETURN_URL": "https://api.example.test/billing/yookassa/return"}
    values.update({key.upper(): value for key, value in changes.items()})
    return Settings(_env_file=None, **values)


def test_canonical_provider_live_and_legacy_alias():
    cfg = config()
    cfg.validate_payment_configuration()
    assert cfg.uses_yookassa
    cfg.payment_provider = "yookassa_web"
    assert cfg.uses_yookassa


@pytest.mark.parametrize("field", ["yookassa_shop_id", "yookassa_secret_key"])
def test_missing_live_credentials_fail(field):
    cfg = config(app_env="production")
    setattr(cfg, field, SecretStr("") if "secret" in field else "")
    with pytest.raises(ValueError, match="shop ID and secret key"):
        cfg.validate_payment_configuration()


def test_production_manual_forbidden():
    cfg = Settings(_env_file=None, APP_ENV="production", PAYMENT_PROVIDER="manual")
    with pytest.raises(ValueError, match="manual is forbidden"):
        cfg.validate_production_configuration()


def test_unknown_receipt_subject_rejected():
    cfg = config()
    cfg.yookassa_receipt_vat_code = "1"
    cfg.yookassa_receipt_payment_subject = "invented_subject"
    cfg.yookassa_receipt_payment_mode = "full_prepayment"
    with pytest.raises(ValueError, match="PAYMENT_SUBJECT"):
        cfg.validate_receipt_configuration()


@pytest.mark.asyncio
async def test_provider_creation_snapshot_and_api_verification():
    ref = str(uuid4())
    calls = []
    def respond(request):
        import json
        calls.append(request)
        if request.method == "POST":
            body = json.loads(request.content)
            assert request.headers["Idempotence-Key"] == ref
            assert body["amount"] == {"value": "499.00", "currency": "RUB"}
            assert body["metadata"]["master_id"] == "7"
            assert body["metadata"]["actor_user_id"] == "8"
            assert body["metadata"]["plan_code"] == "basic_monthly"
            return httpx.Response(200, json={"id": "remote-1", "status": "pending", "paid": False,
                "amount": body["amount"], "metadata": body["metadata"],
                "confirmation": {"confirmation_url": "https://yookassa.ru/checkout/test"}})
        return httpx.Response(200, json={"id": "remote-1", "status": "succeeded", "paid": True,
            "amount": {"value": "499.00", "currency": "RUB"}, "metadata": {"checkout_ref": ref}})
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
        provider = YooKassaProvider(YooKassaClient("123", "never-log-this", client=http))
        plan = SimpleNamespace(id=9, price=Decimal("499"), currency="RUB", name="Basic",
                               period_days=30, code="basic_monthly")
        intent = await provider.create_payment_intent(7, plan, "https://example.test/return",
                         {"checkout_ref": ref, "payment_id": 10, "actor_user_id": 8})
        assert intent.payment_url == "https://yookassa.ru/checkout/test"
        result = await provider.verify_callback({"object": {"id": "remote-1", "amount": "forged"}})
        assert result.is_valid and result.status == "SUCCEEDED"
        assert len(calls) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("status,paid,expected,valid", [
    ("pending", False, "PENDING", True),
    ("waiting_for_capture", False, "PENDING", True),
    ("canceled", False, "CANCELLED", True),
    ("succeeded", False, "FAILED", False),
])
async def test_provider_statuses(status, paid, expected, valid):
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200,
        json={"id": "remote-1", "status": status, "paid": paid,
              "amount": {"value": "499.00", "currency": "RUB"}, "metadata": {}}))) as http:
        result = await YooKassaProvider(YooKassaClient("123", "secret", client=http)).get_payment_status("remote-1")
        assert (result.status, result.is_valid) == (expected, valid)


@pytest.mark.asyncio
async def test_timeout_does_not_expose_secret(caplog):
    secret = "test-secret-never-print"
    def fail(request):
        raise httpx.ReadTimeout(secret, request=request)
    async with httpx.AsyncClient(transport=httpx.MockTransport(fail)) as http:
        provider = YooKassaProvider(YooKassaClient("123", secret, client=http))
        with pytest.raises(YooKassaGatewayError) as caught:
            await provider.get_payment_status("remote-1")
        assert secret not in str(caught.value) and secret not in caplog.text


@pytest.mark.parametrize("mode,allow,accepted", [("test", False, False), ("test", True, True), ("live", True, True)])
def test_production_mode_opt_in_is_explicit(mode, allow, accepted):
    cfg = config(app_env="production", yookassa_mode=mode,
                 yookassa_allow_test_in_production=allow,
                 yookassa_test_allowed_telegram_ids=[2147176678],
                 yookassa_test_shop_id="test-shop", yookassa_test_secret_key="test-key")
    if accepted:
        cfg.validate_payment_configuration()
        if mode == "test":
            assert cfg.can_use_yookassa_test_checkout(2147176678)
            assert not cfg.can_use_yookassa_test_checkout(12345)
    else:
        with pytest.raises(ValueError, match="ALLOW_TEST_IN_PRODUCTION"):
            cfg.validate_payment_configuration()


def test_remote_test_flag_cannot_activate_live_order(monkeypatch):
    from app.config.settings import settings
    from app.services.billing.yookassa_checkout import YooKassaCheckoutService
    from app.services.billing.yookassa_client import YooKassaPayment
    from app.services.exceptions import SubscriptionError
    ref = uuid4()
    monkeypatch.setattr(settings, "yookassa_mode", "live")
    remote = YooKassaPayment("remote-1", "succeeded", True, Decimal("499"), "RUB", str(ref), None, test=True)
    with pytest.raises(SubscriptionError, match="Режим"):
        YooKassaCheckoutService._verify_remote(remote, ref, Decimal("499"), "RUB",
                                              payment_id=1, user_id=2, plan_id=3)
