"""
Configuration settings for the application using Pydantic Settings v2.
"""

from typing import List
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


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
    postgres_host: str = Field(default="localhost", alias="POSTGRES_HOST")
    postgres_port: int = Field(default=5432, alias="POSTGRES_PORT")
    postgres_db: str = Field(default="beauty_bot_db", alias="POSTGRES_DB")
    postgres_user: str = Field(default="postgres", alias="POSTGRES_USER")
    postgres_password: str = Field(default="postgres_secure_password", alias="POSTGRES_PASSWORD")

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
    default_bank_name: str = Field(default="Сбербанк", alias="DEFAULT_BANK_NAME")
    default_card_number: str = Field(default="2202 2000 0000 0000", alias="DEFAULT_CARD_NUMBER")
    default_phone_requisites: str = Field(default="+7 (999) 000-00-00", alias="DEFAULT_PHONE_REQUISITES")
    default_recipient_name: str = Field(default="Иван И.", alias="DEFAULT_RECIPIENT_NAME")

    @field_validator("admin_ids", mode="before")
    @classmethod
    def parse_admin_ids(cls, v):
        if isinstance(v, str):
            if not v.strip():
                return []
            return [int(x.strip()) for x in v.split(",") if x.strip().isdigit()]
        elif isinstance(v, int):
            return [v]
        elif isinstance(v, list):
            return [int(x) for x in v]
        return []

    @property
    def database_url(self) -> str:
        """
        Constructs asyncpg database connection URL.
        """
        return (
            f"postgresql+asyncpg://{self.postgres_user}:{self.postgres_password}@"
            f"{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def sync_database_url(self) -> str:
        """
        Constructs psycopg/sync database connection URL for Alembic migrations if needed.
        """
        return (
            f"postgresql+psycopg2://{self.postgres_user}:{self.postgres_password}@"
            f"{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def redis_url(self) -> str:
        """
        Constructs Redis connection URL.
        """
        if self.redis_password:
            return f"redis://:{self.redis_password}@{self.redis_host}:{self.redis_port}/{self.redis_db}"
        return f"redis://{self.redis_host}:{self.redis_port}/{self.redis_db}"


settings = Settings()
