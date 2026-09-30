"""
Configuration settings for the application using Pydantic Settings v2.
"""

from pathlib import Path
from typing import List
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import URL, make_url


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

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
    bank_name: str = Field(default="Сбербанк", alias="BANK_NAME")
    bank_card_number: str = Field(default="2202 2000 0000 0000", alias="BANK_CARD_NUMBER")
    default_phone_requisites: str = Field(default="+7 (999) 000-00-00", alias="DEFAULT_PHONE_REQUISITES")
    bank_recipient_name: str = Field(default="Иван И.", alias="BANK_RECIPIENT_NAME")

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

    # Phase 8: Multi-Replica Multi-Tenant Scheduler & Reliable Background Jobs
    scheduler_enabled: bool = Field(default=True, alias="SCHEDULER_ENABLED")
    scheduler_tick_seconds: int = Field(default=30, alias="SCHEDULER_TICK_SECONDS")
    scheduler_batch_size: int = Field(default=100, alias="SCHEDULER_BATCH_SIZE")
    job_processing_timeout_seconds: int = Field(default=300, alias="JOB_PROCESSING_TIMEOUT_SECONDS")
    job_max_attempts: int = Field(default=5, alias="JOB_MAX_ATTEMPTS")
    hold_cleaner_interval_seconds: int = Field(default=60, alias="HOLD_CLEANER_INTERVAL_SECONDS")
    reminder_generation_interval_seconds: int = Field(default=120, alias="REMINDER_GENERATION_INTERVAL_SECONDS")
    reminder_delivery_interval_seconds: int = Field(default=30, alias="REMINDER_DELIVERY_INTERVAL_SECONDS")

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
            return f"redis://:{self.redis_password}@{self.redis_host}:{self.redis_port}/{self.redis_db}"
        return f"redis://{self.redis_host}:{self.redis_port}/{self.redis_db}"


settings = Settings()
