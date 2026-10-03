"""
Configuration settings for the application using Pydantic Settings v2.
"""

from pathlib import Path
from decimal import Decimal
from typing import List, Optional
import re
from urllib.parse import quote, urlsplit
from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import URL, make_url


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Environment
    app_env: str = Field(default="development", alias="APP_ENV")

    # Telegram Bot
    bot_token: str = Field(default="dummy_token_for_init", alias="BOT_TOKEN")
    admin_ids: List[int] = Field(default_factory=list, alias="ADMIN_IDS")

    # PostgreSQL Database
    database_url: str = Field(
        default="postgresql+asyncpg://postgres:postgres@localhost:5432/beauty_bot",
        alias="DATABASE_URL",
    )
    db_pool_size: int = Field(default=10, alias="DB_POOL_SIZE")
    db_max_overflow: int = Field(default=20, alias="DB_MAX_OVERFLOW")
    db_pool_timeout: int = Field(default=30, alias="DB_POOL_TIMEOUT")
    db_pool_recycle: int = Field(default=1800, alias="DB_POOL_RECYCLE")
    db_pool_pre_ping: bool = Field(default=True, alias="DB_POOL_PRE_PING")

    # Redis
    redis_host: str = Field(default="localhost", alias="REDIS_HOST")
    redis_port: int = Field(default=6379, alias="REDIS_PORT")
    redis_db: int = Field(default=0, alias="REDIS_DB")
    redis_password: str | None = Field(default=None, alias="REDIS_PASSWORD")

    # App Preferences & Defaults
    timezone: str = Field(default="Europe/Moscow", alias="TIMEZONE")
    hold_duration_minutes: int = Field(default=30, alias="HOLD_DURATION_MINUTES")
    min_advance_hours: int = Field(default=2, alias="MIN_ADVANCE_HOURS")
    max_advance_days: int = Field(default=30, alias="MAX_ADVANCE_DAYS")
    default_buffer_minutes: int = Field(default=30, alias="DEFAULT_BUFFER_MINUTES")
    grid_step_minutes: int = Field(default=30, alias="GRID_STEP_MINUTES")

    # Requisites
    # Client prepayment requisites are tenant-specific and must never have
    # plausible-looking global defaults that could send money to a sample account.
    bank_name: Optional[str] = Field(default=None, alias="BANK_NAME")
    bank_card_number: Optional[str] = Field(default=None, alias="BANK_CARD_NUMBER")
    default_phone_requisites: Optional[str] = Field(default=None, alias="DEFAULT_PHONE_REQUISITES")
    bank_recipient_name: Optional[str] = Field(default=None, alias="BANK_RECIPIENT_NAME")

    # Phase 5: Dynamic Bot Instances, Token Security & BotRegistry
    bot_token_encryption_key: str = Field(
        default="",
        alias="BOT_TOKEN_ENCRYPTION_KEY",
    )
    bot_registry_cache_max_size: int = Field(default=500, alias="BOT_REGISTRY_CACHE_MAX_SIZE")
    bot_registry_cache_ttl_seconds: int = Field(default=300, alias="BOT_REGISTRY_CACHE_TTL_SECONDS")
    bot_registry_invalidation_channel: str = Field(
        default="bot_registry:invalidate", alias="BOT_REGISTRY_INVALIDATION_CHANNEL"
    )

    # Phase 6: Multi-Bot Webhook Ingestion Engine
    app_mode: str = Field(default="polling", alias="APP_MODE")
    webhook_base_url: str = Field(default="", alias="WEBHOOK_BASE_URL")
    webhook_host: str = Field(default="0.0.0.0", alias="WEBHOOK_HOST")
    webhook_port: int = Field(default=8000, alias="WEBHOOK_PORT")
    webhook_max_body_bytes: int = Field(default=1_048_576, alias="WEBHOOK_MAX_BODY_BYTES")
    webhook_update_dedup_ttl: int = Field(default=86400, alias="WEBHOOK_UPDATE_DEDUP_TTL")

    # Phase 7: Platform Manager Bot & Onboarding
    manager_bot_token: str = Field(default="", alias="MANAGER_BOT_TOKEN")
    manager_webhook_secret: str = Field(default="", alias="MANAGER_WEBHOOK_SECRET")
    trial_duration_days: int = Field(default=14, alias="TRIAL_DURATION_DAYS")
    support_telegram_username: str = Field(default="zapisflow", alias="SUPPORT_TELEGRAM_USERNAME")
    payment_provider: str = Field(default="manual", alias="PAYMENT_PROVIDER")
    payment_currency: str = Field(default="RUB", alias="PAYMENT_CURRENCY")
    yookassa_mode: str = Field(default="test", alias="YOOKASSA_MODE")
    yookassa_allow_test_in_production: bool = Field(
        default=False, alias="YOOKASSA_ALLOW_TEST_IN_PRODUCTION"
    )
    yookassa_test_allowed_telegram_ids: List[int] = Field(
        default_factory=list, alias="YOOKASSA_TEST_ALLOWED_TELEGRAM_IDS"
    )
    yookassa_shop_id: str = Field(default="", alias="YOOKASSA_SHOP_ID")
    yookassa_secret_key: SecretStr = Field(default=SecretStr(""), alias="YOOKASSA_SECRET_KEY")
    yookassa_test_shop_id: str = Field(default="", alias="YOOKASSA_TEST_SHOP_ID")
    yookassa_test_secret_key: SecretStr = Field(default=SecretStr(""), alias="YOOKASSA_TEST_SECRET_KEY")
    billing_return_url: str = Field(default="", alias="BILLING_RETURN_URL")
    yookassa_fiscal_mode: str = Field(default="self_employed", alias="YOOKASSA_FISCAL_MODE")
    yookassa_receipt_vat_code: str = Field(default="", alias="YOOKASSA_RECEIPT_VAT_CODE")
    yookassa_receipt_payment_subject: str = Field(default="", alias="YOOKASSA_RECEIPT_PAYMENT_SUBJECT")
    yookassa_receipt_payment_mode: str = Field(default="", alias="YOOKASSA_RECEIPT_PAYMENT_MODE")
    yookassa_reconciliation_interval_seconds: int = Field(
        default=300, alias="YOOKASSA_RECONCILIATION_INTERVAL_SECONDS"
    )

    # Phase 8: Multi-Replica Multi-Tenant Scheduler & Reliable Background Jobs
    scheduler_enabled: bool = Field(default=True, alias="SCHEDULER_ENABLED")
    scheduler_tick_seconds: int = Field(default=30, alias="SCHEDULER_TICK_SECONDS")
    scheduler_batch_size: int = Field(default=100, alias="SCHEDULER_BATCH_SIZE")
    job_processing_timeout_seconds: int = Field(default=300, alias="JOB_PROCESSING_TIMEOUT_SECONDS")
    job_max_attempts: int = Field(default=5, alias="JOB_MAX_ATTEMPTS")
    hold_cleaner_interval_seconds: int = Field(default=60, alias="HOLD_CLEANER_INTERVAL_SECONDS")
    reminder_generation_interval_seconds: int = Field(default=120, alias="REMINDER_GENERATION_INTERVAL_SECONDS")
    reminder_delivery_interval_seconds: int = Field(default=30, alias="REMINDER_DELIVERY_INTERVAL_SECONDS")
    telegram_outbox_poll_interval_seconds: int = Field(
        default=2, ge=1, le=60, alias="TELEGRAM_OUTBOX_POLL_INTERVAL_SECONDS"
    )

    @property
    def is_production(self) -> bool:
        """Returns True if the application is running in production mode."""
        return self.app_env.strip().lower() in ("production", "prod")

    @property
    def support_url(self) -> str:
        """Direct link to support chat in Telegram."""
        username = self.support_telegram_username.strip().lstrip("@")
        return f"https://t.me/{username}"

    @property
    def support_tag(self) -> str:
        """Formatted @username handle for support."""
        username = self.support_telegram_username.strip().lstrip("@")
        return f"@{username}"

    @property
    def yookassa_credentials(self) -> tuple[str, str]:
        """Select one credential pair without ever mixing test and live shops."""
        if self.yookassa_mode.lower() == "live":
            return self.yookassa_shop_id, self.yookassa_secret_key.get_secret_value()
        if self.yookassa_mode.lower() == "test":
            return self.yookassa_test_shop_id, self.yookassa_test_secret_key.get_secret_value()
        raise ValueError("YOOKASSA_MODE must be 'test' or 'live'")

    @property
    def billing_site_origin(self) -> str:
        """Return the independently served website origin, never a browser value."""
        parsed = urlsplit(self.billing_return_url)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError("BILLING_RETURN_URL must be a valid HTTPS URL")
        return f"{parsed.scheme}://{parsed.netloc}"

    def can_use_yookassa_test_checkout(self, telegram_id: int) -> bool:
        """Keep the production test shop limited to explicitly named owners."""
        if not self.is_production or self.yookassa_mode.lower() != "test":
            return True
        return (
            self.yookassa_allow_test_in_production
            and telegram_id in self.yookassa_test_allowed_telegram_ids
        )

    def build_yookassa_receipt(self, *, email: str, description: str, amount: Decimal) -> dict:
        """Use merchant-confirmed fiscal settings, with no invented VAT values."""
        self.validate_receipt_configuration()
        return {
            "customer": {"email": email},
            "items": [{
                "description": description[:128],
                "quantity": "1.00",
                "amount": {"value": f"{amount:.2f}", "currency": "RUB"},
                "vat_code": int(self.yookassa_receipt_vat_code),
                "payment_subject": self.yookassa_receipt_payment_subject,
                "payment_mode": self.yookassa_receipt_payment_mode,
            }],
        }

    def validate_receipt_configuration(self) -> None:
        if not self.yookassa_receipt_vat_code.isdigit() or int(self.yookassa_receipt_vat_code) not in range(1, 13):
            raise ValueError("YOOKASSA_RECEIPT_VAT_CODE must be merchant-confirmed (1-12)")
        if not re.fullmatch(r"[a-z_]{2,32}", self.yookassa_receipt_payment_subject):
            raise ValueError("YOOKASSA_RECEIPT_PAYMENT_SUBJECT must be merchant-confirmed")
        if self.yookassa_receipt_payment_mode not in {"full_payment", "full_prepayment"}:
            raise ValueError("YOOKASSA_RECEIPT_PAYMENT_MODE must be merchant-confirmed")

    def validate_payment_configuration(self) -> None:
        """Fail closed when YooKassa is enabled without its dependencies."""
        if self.payment_provider.lower() != "yookassa_web":
            return
        errors: list[str] = []
        if self.payment_currency != "RUB":
            errors.append("PAYMENT_CURRENCY must be RUB for YooKassa checkout")
        if self.yookassa_mode.lower() not in {"test", "live"}:
            errors.append("YOOKASSA_MODE must be 'test' or 'live'")
        elif self.is_production and self.yookassa_mode.lower() == "test" and not self.yookassa_allow_test_in_production:
            errors.append("YOOKASSA_ALLOW_TEST_IN_PRODUCTION=true is required for production test mode")
        else:
            shop_id, secret_key = self.yookassa_credentials
            if not shop_id or not secret_key:
                errors.append("YooKassa shop ID and secret key are required for the selected mode")
        if self.is_production and self.yookassa_mode.lower() == "test" and self.yookassa_allow_test_in_production:
            if not self.yookassa_test_allowed_telegram_ids or any(
                user_id <= 0 for user_id in self.yookassa_test_allowed_telegram_ids
            ):
                errors.append("YOOKASSA_TEST_ALLOWED_TELEGRAM_IDS must contain a test owner")
        parsed = urlsplit(self.billing_return_url)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            errors.append("BILLING_RETURN_URL must be an HTTPS URL without credentials")
        else:
            main_site_return = parsed.netloc == "zapisflow.su" and parsed.path in {"", "/"}
            billing_return = parsed.path in {"/billing/success", "/billing/yookassa/return"}
            if not (main_site_return or billing_return) or parsed.query or parsed.fragment:
                errors.append("BILLING_RETURN_URL must point to a supported billing return path")
        if self.yookassa_fiscal_mode not in {"self_employed", "merchant_receipt"}:
            errors.append("YOOKASSA_FISCAL_MODE must be self_employed or merchant_receipt")
        elif self.yookassa_fiscal_mode == "merchant_receipt":
            try:
                self.validate_receipt_configuration()
            except ValueError as exc:
                errors.append(str(exc))
        if errors:
            raise ValueError("Payment configuration invalid: " + "; ".join(errors))

    def validate_production_configuration(self) -> None:
        """
        Strict validation for production deployment.
        Enforces:
        - APP_MODE == 'webhook'
        - WEBHOOK_BASE_URL starts with https:// and is not empty
        - BOT_TOKEN_ENCRYPTION_KEY is valid 32-byte key and not a placeholder
        - MANAGER_WEBHOOK_SECRET is non-empty and at least 32 chars
        - MANAGER_BOT_TOKEN is non-empty
        """
        if not self.is_production:
            return

        self.validate_payment_configuration()

        errors: list[str] = []

        if self.app_mode.lower() != "webhook":
            errors.append(f"APP_MODE must be 'webhook' in production, got '{self.app_mode}'")

        if not self.webhook_base_url or not self.webhook_base_url.startswith("https://"):
            errors.append("WEBHOOK_BASE_URL must be configured with https:// in production")

        if not self.manager_bot_token:
            errors.append("MANAGER_BOT_TOKEN is required in production")

        if not self.manager_webhook_secret or len(self.manager_webhook_secret) < 32:
            errors.append("MANAGER_WEBHOOK_SECRET must be at least 32 characters in production")

        if not self.redis_password:
            errors.append("REDIS_PASSWORD is required in production")
        elif not re.fullmatch(r"[A-Za-z0-9_-]+", self.redis_password):
            errors.append("REDIS_PASSWORD must use only URL-safe letters, digits, '_' or '-'")

        if not self.bot_token_encryption_key:
            errors.append("BOT_TOKEN_ENCRYPTION_KEY is required in production")
        else:
            try:
                from app.core.token_crypto import TokenCrypto
                key_bytes = TokenCrypto._parse_key(self.bot_token_encryption_key)
                if len(key_bytes) != 32:
                    errors.append("BOT_TOKEN_ENCRYPTION_KEY must decode to exactly 32 bytes")
            except Exception as e:
                errors.append(f"BOT_TOKEN_ENCRYPTION_KEY invalid: {e}")

        if "postgres:postgres@localhost" in self.database_url:
            errors.append("DATABASE_URL uses default insecure credentials in production")

        if errors:
            raise ValueError(
                "Production configuration validation failed:\n" + "\n".join(f" - {err}" for err in errors)
            )

    @property
    def sync_database_url(self) -> str:
        """Synchronous URL for migrations/tools if needed."""
        url = self.database_url
        if "+asyncpg" in url:
            return url.replace("+asyncpg", "")
        return url

    @property
    def safe_database_url(self) -> str:
        """Database URL with obscured password for logs."""
        try:
            parsed = make_url(self.database_url)
            return parsed.render_as_string(hide_password=True)
        except Exception:
            return "postgresql://***@***"

    @property
    def redis_url(self) -> str:
        """
        Constructs Redis connection URL.
        """
        if self.redis_password:
            encoded_password = quote(self.redis_password, safe="")
            return f"redis://:{encoded_password}@{self.redis_host}:{self.redis_port}/{self.redis_db}"
        return f"redis://{self.redis_host}:{self.redis_port}/{self.redis_db}"


settings = Settings()
