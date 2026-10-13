"""The committed environment example must parse with the real settings class."""

from pathlib import Path
from decimal import Decimal
import importlib

import pytest

from app.config.settings import Settings
from sqlalchemy.engine import make_url

ROOT = Path(__file__).resolve().parents[1]


def test_admin_ids_in_env_example_parse_as_json(monkeypatch) -> None:
    monkeypatch.delenv("ADMIN_IDS", raising=False)
    configured = Settings(_env_file=ROOT / ".env.example")
    assert configured.admin_ids == [123456789, 987654321]


def test_database_url_and_pool_settings() -> None:
    configured = Settings(
        _env_file=None,
        DATABASE_URL="postgresql+asyncpg://myuser:mypass@db.host:5432/beauty_db",
        DB_POOL_SIZE=15,
        DB_MAX_OVERFLOW=25,
        DB_POOL_TIMEOUT=45,
    )
    assert make_url(configured.database_url).drivername == "postgresql+asyncpg"
    assert make_url(configured.database_url).database == "beauty_db"
    assert make_url(configured.sync_database_url).drivername == "postgresql"
    assert configured.db_pool_size == 15
    assert configured.db_max_overflow == 25
    assert configured.db_pool_timeout == 45
    assert "mypass" not in configured.safe_database_url


def test_asyncpg_is_in_canonical_dependencies() -> None:
    requirements = (ROOT / "requirements.txt").read_text(encoding="utf-8")
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert "asyncpg==" in requirements
    assert "asyncpg>=" in pyproject
    assert importlib.import_module("asyncpg")


def test_redis_password_is_used_and_required_in_production() -> None:
    configured = Settings(_env_file=None, REDIS_PASSWORD="safe_password-1")
    assert configured.redis_url == "redis://:safe_password-1@localhost:6379/0"

    production = Settings(
        _env_file=None,
        APP_ENV="production",
        APP_MODE="webhook",
        REDIS_PASSWORD="",
    )
    with pytest.raises(ValueError, match="REDIS_PASSWORD is required"):
        production.validate_production_configuration()

    invalid = Settings(
        _env_file=None,
        APP_ENV="production",
        APP_MODE="webhook",
        REDIS_PASSWORD="bad password",
    )
    with pytest.raises(ValueError, match="REDIS_PASSWORD must use only URL-safe"):
        invalid.validate_production_configuration()


def test_yookassa_web_checkout_requires_selected_mode_credentials() -> None:
    missing = Settings(
        _env_file=None,
        PAYMENT_PROVIDER="yookassa_web",
        YOOKASSA_MODE="test",
        BILLING_RETURN_URL="https://pay.example.test/billing/success",
    )
    with pytest.raises(ValueError, match="shop ID and secret key"):
        missing.validate_payment_configuration()

    configured = Settings(
        _env_file=None,
        PAYMENT_PROVIDER="yookassa_web",
        YOOKASSA_MODE="test",
        YOOKASSA_TEST_SHOP_ID="test-shop",
        YOOKASSA_TEST_SECRET_KEY="test-secret",
        YOOKASSA_SHOP_ID="live-shop",
        YOOKASSA_SECRET_KEY="live-secret",
        BILLING_RETURN_URL="https://pay.example.test/billing/success",
        YOOKASSA_RECEIPT_VAT_CODE="1",
        YOOKASSA_RECEIPT_PAYMENT_SUBJECT="service",
        YOOKASSA_RECEIPT_PAYMENT_MODE="full_payment",
    )
    configured.validate_payment_configuration()
    assert configured.yookassa_credentials == ("test-shop", "test-secret")
    assert "test-secret" not in repr(configured)


def test_yookassa_production_rejects_test_mode_without_leaking_credentials() -> None:
    configured = Settings(
        _env_file=None,
        APP_ENV="production",
        PAYMENT_PROVIDER="yookassa_web",
        YOOKASSA_MODE="test",
        YOOKASSA_TEST_SHOP_ID="test-shop",
        YOOKASSA_TEST_SECRET_KEY="never-print-this-secret",
        BILLING_RETURN_URL="https://pay.example.test/billing/success",
    )
    with pytest.raises(ValueError, match="YOOKASSA_ALLOW_TEST_IN_PRODUCTION=true") as caught:
        configured.validate_payment_configuration()
    assert "never-print-this-secret" not in str(caught.value)

    missing_live = Settings(
        _env_file=None,
        APP_ENV="production",
        PAYMENT_PROVIDER="yookassa_web",
        YOOKASSA_MODE="live",
        BILLING_RETURN_URL="https://pay.example.test/billing/success",
    )
    with pytest.raises(ValueError, match="shop ID and secret key"):
        missing_live.validate_production_configuration()


def test_yookassa_production_test_mode_requires_opt_in_owner_and_test_credentials() -> None:
    values = dict(
        APP_ENV="production",
        PAYMENT_PROVIDER="yookassa_web",
        YOOKASSA_MODE="test",
        BILLING_RETURN_URL="https://pay.zapisflow.su/billing/success",
        YOOKASSA_RECEIPT_VAT_CODE="1",
        YOOKASSA_RECEIPT_PAYMENT_SUBJECT="service",
        YOOKASSA_RECEIPT_PAYMENT_MODE="full_prepayment",
        YOOKASSA_FISCAL_MODE="merchant_receipt",
        YOOKASSA_ALLOW_TEST_IN_PRODUCTION=True,
        YOOKASSA_TEST_ALLOWED_TELEGRAM_IDS=[2147176678],
        YOOKASSA_TEST_SHOP_ID="test-shop",
        YOOKASSA_TEST_SECRET_KEY="test-secret",
    )
    configured = Settings(_env_file=None, **values)
    configured.validate_payment_configuration()
    assert configured.yookassa_credentials == ("test-shop", "test-secret")
    assert configured.can_use_yookassa_test_checkout(2147176678)
    assert not configured.can_use_yookassa_test_checkout(2147176679)
    receipt = configured.build_yookassa_receipt(
        email="owner@example.com", description="ZapisFlow Basic", amount=Decimal("499.00"),
    )
    assert receipt["items"][0]["payment_subject"] == "service"
    assert receipt["items"][0]["payment_mode"] == "full_prepayment"
    assert receipt["items"][0]["vat_code"] == 1

    with pytest.raises(ValueError, match="shop ID and secret key"):
        Settings(_env_file=None, **{**values, "YOOKASSA_TEST_SECRET_KEY": ""}).validate_payment_configuration()
    with pytest.raises(ValueError, match="YOOKASSA_TEST_ALLOWED_TELEGRAM_IDS"):
        Settings(_env_file=None, **{**values, "YOOKASSA_TEST_ALLOWED_TELEGRAM_IDS": []}).validate_payment_configuration()
    with pytest.raises(ValueError, match="YOOKASSA_RECEIPT_VAT_CODE"):
        Settings(_env_file=None, **{**values, "YOOKASSA_RECEIPT_VAT_CODE": ""}).validate_payment_configuration()
    with pytest.raises(ValueError, match="YOOKASSA_RECEIPT_PAYMENT_MODE"):
        Settings(_env_file=None, **{**values, "YOOKASSA_RECEIPT_PAYMENT_MODE": "invented"}).validate_payment_configuration()


@pytest.mark.parametrize("test_opt_in", [False, True])
def test_yookassa_production_live_mode_ignores_test_opt_in(test_opt_in: bool) -> None:
    configured = Settings(
        _env_file=None,
        APP_ENV="production",
        PAYMENT_PROVIDER="yookassa_web",
        YOOKASSA_MODE="live",
        YOOKASSA_ALLOW_TEST_IN_PRODUCTION=test_opt_in,
        YOOKASSA_TEST_ALLOWED_TELEGRAM_IDS=[],
        YOOKASSA_SHOP_ID="live-shop",
        YOOKASSA_SECRET_KEY="live-secret",
        BILLING_RETURN_URL="https://pay.zapisflow.su/billing/success",
        YOOKASSA_RECEIPT_VAT_CODE="1",
        YOOKASSA_RECEIPT_PAYMENT_SUBJECT="service",
        YOOKASSA_RECEIPT_PAYMENT_MODE="full_prepayment",
    )
    configured.validate_payment_configuration()
    assert configured.yookassa_credentials == ("live-shop", "live-secret")


def test_yookassa_receipt_requires_merchant_settings_and_preserves_price() -> None:
    missing = Settings(
        _env_file=None,
        PAYMENT_PROVIDER="yookassa_web",
        YOOKASSA_MODE="test",
        YOOKASSA_TEST_SHOP_ID="test-shop",
        YOOKASSA_TEST_SECRET_KEY="test-secret",
        BILLING_RETURN_URL="https://pay.zapisflow.su/billing/success",
        YOOKASSA_FISCAL_MODE="merchant_receipt",
    )
    with pytest.raises(ValueError, match="YOOKASSA_RECEIPT_VAT_CODE"):
        missing.validate_payment_configuration()

    configured = Settings(
        _env_file=None,
        PAYMENT_PROVIDER="yookassa_web",
        YOOKASSA_MODE="test",
        YOOKASSA_TEST_SHOP_ID="test-shop",
        YOOKASSA_TEST_SECRET_KEY="test-secret",
        BILLING_RETURN_URL="https://pay.zapisflow.su/billing/success",
        YOOKASSA_RECEIPT_VAT_CODE="1",
        YOOKASSA_RECEIPT_PAYMENT_SUBJECT="service",
        YOOKASSA_RECEIPT_PAYMENT_MODE="full_payment",
        YOOKASSA_FISCAL_MODE="merchant_receipt",
    )
    configured.validate_payment_configuration()
    receipt = configured.build_yookassa_receipt(
        email="buyer@example.com", description="ZapisFlow Basic", amount=Decimal("499.00"),
    )
    assert receipt["customer"]["email"] == "buyer@example.com"
    assert receipt["items"][0]["amount"] == {"value": "499.00", "currency": "RUB"}
    assert receipt["items"][0]["vat_code"] == 1
    assert configured.billing_site_origin == "https://pay.zapisflow.su"


def test_billing_host_is_separate_from_existing_website() -> None:
    example = (ROOT / ".env.example").read_text(encoding="utf-8")
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    caddy = (ROOT / "deploy/caddy/Caddyfile").read_text(encoding="utf-8")

    assert "BILLING_DOMAIN=pay.zapisflow.su" in example
    assert "BILLING_RETURN_URL=https://zapisflow.su/" in example
    assert "${BILLING_DOMAIN:-pay.zapisflow.su}" in compose
    assert "{$BILLING_DOMAIN:pay.zapisflow.su}" in caddy
    assert "{$BILLING_DOMAIN:zapisflow.su}" not in caddy
    assert "request>headers>X-Telegram-Bot-Api-Secret-Token delete" in caddy
    assert "log_skip @telegram_webhooks" in caddy


def test_self_employed_yookassa_works_without_fiscal_receipt_fields() -> None:
    configured = Settings(
        _env_file=None,
        APP_ENV="production",
        PAYMENT_PROVIDER="yookassa_web",
        YOOKASSA_MODE="test",
        YOOKASSA_ALLOW_TEST_IN_PRODUCTION=True,
        YOOKASSA_TEST_ALLOWED_TELEGRAM_IDS=[2147176678],
        YOOKASSA_TEST_SHOP_ID="test-shop",
        YOOKASSA_TEST_SECRET_KEY="test-secret",
        BILLING_RETURN_URL="https://zapisflow.su/",
        YOOKASSA_FISCAL_MODE="self_employed",
    )
    configured.validate_payment_configuration()
    assert configured.yookassa_fiscal_mode == "self_employed"


def test_main_site_return_url_must_be_exact_https_origin() -> None:
    base = dict(
        _env_file=None, PAYMENT_PROVIDER="yookassa_web", YOOKASSA_MODE="test",
        YOOKASSA_TEST_SHOP_ID="test-shop", YOOKASSA_TEST_SECRET_KEY="test-secret",
        YOOKASSA_FISCAL_MODE="self_employed",
    )
    Settings(**base, BILLING_RETURN_URL="https://zapisflow.su/").validate_payment_configuration()
    for url in ("http://zapisflow.su/", "https://zapisflow.su/evil", "https://zapisflow.su:8443/"):
        with pytest.raises(ValueError, match="BILLING_RETURN_URL"):
            Settings(**base, BILLING_RETURN_URL=url).validate_payment_configuration()


def test_unknown_fiscal_mode_fails_closed() -> None:
    configured = Settings(
        _env_file=None, PAYMENT_PROVIDER="yookassa_web", YOOKASSA_MODE="test",
        YOOKASSA_TEST_SHOP_ID="test-shop", YOOKASSA_TEST_SECRET_KEY="test-secret",
        BILLING_RETURN_URL="https://api.zapisflow.su/billing/yookassa/return",
        YOOKASSA_FISCAL_MODE="unsupported",
    )
    with pytest.raises(ValueError, match="YOOKASSA_FISCAL_MODE"):
        configured.validate_payment_configuration()
