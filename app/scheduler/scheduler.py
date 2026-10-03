"""Multi-replica aware scheduler and periodic background jobs orchestrator.

Manages periodic jobs for:
- Hold expiration cleaner (PostgreSQL SKIP LOCKED)
- Client visit reminder generation (idempotent ON CONFLICT)
- Client visit reminder delivery worker (PostgreSQL SKIP LOCKED & BotRegistry routing)
"""

import logging
from typing import Optional
from aiogram import Bot
from apscheduler.schedulers.asyncio import AsyncIOScheduler
import pytz
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.config.settings import settings
from app.database.session import async_session_maker
from app.scheduler.jobs.hold_cleaner import clean_expired_holds
from app.scheduler.jobs.reminder_generator import generate_visit_reminders
from app.scheduler.jobs.reminder_worker import send_visit_reminders
from app.scheduler.jobs.subscription_worker import refresh_subscriptions
from app.scheduler.jobs.broadcast_worker import resume_broadcasts
from app.scheduler.jobs.telegram_outbox_worker import dispatch_telegram_outbox
from app.scheduler.jobs.yookassa_reconciliation import reconcile_pending_yookassa
from app.services.bot_registry import BotRegistry

logger = logging.getLogger("app.scheduler")


class MultiTenantScheduler:
    """Multi-replica background scheduler for tenant jobs."""

    def __init__(
        self,
        registry: Optional[BotRegistry] = None,
        bot: Optional[Bot] = None,
        session_maker: async_sessionmaker = async_session_maker,
    ) -> None:
        self.registry = registry
        self.bot = bot
        self.session_maker = session_maker
        self._scheduler = AsyncIOScheduler(timezone=pytz.timezone(settings.timezone))
        self._setup_jobs()

    def _setup_jobs(self) -> None:
        """Register periodic jobs based on application settings."""
        # 1. Hold cleaner job
        self._scheduler.add_job(
            clean_expired_holds,
            trigger="interval",
            seconds=settings.hold_cleaner_interval_seconds,
            id="clean_expired_holds",
            name="Clean Expired Booking Holds",
            replace_existing=True,
            kwargs={
                "bot": self.bot,
                "registry": self.registry,
                "session_maker": self.session_maker,
                "batch_size": settings.scheduler_batch_size,
            },
        )

        # 2. Reminder generator job
        self._scheduler.add_job(
            generate_visit_reminders,
            trigger="interval",
            seconds=settings.reminder_generation_interval_seconds,
            id="generate_visit_reminders",
            name="Generate Upcoming Visit Reminders",
            replace_existing=True,
            kwargs={
                "session_maker": self.session_maker,
            },
        )

        # 3. Reminder delivery worker job
        self._scheduler.add_job(
            send_visit_reminders,
            trigger="interval",
            seconds=settings.reminder_delivery_interval_seconds,
            id="send_visit_reminders",
            name="Dispatch Due Visit Reminders",
            replace_existing=True,
            kwargs={
                "bot": self.bot,
                "registry": self.registry,
                "session_maker": self.session_maker,
                "batch_size": settings.scheduler_batch_size,
                "max_attempts": settings.job_max_attempts,
                "auto_generate": False,
            },
        )

        # 4. Subscription expiration worker job
        self._scheduler.add_job(
            refresh_subscriptions,
            trigger="interval",
            seconds=getattr(settings, "subscription_refresh_interval_seconds", 600),
            id="refresh_subscriptions",
            name="Refresh Expired SaaS Subscriptions",
            replace_existing=True,
            kwargs={
                "session_maker": self.session_maker,
                "batch_size": settings.scheduler_batch_size,
            },
        )

        # Campaign recipients and one-off Telegram notifications are durable
        # work units and continue after webhook completion or process restart.
        self._scheduler.add_job(
            resume_broadcasts,
            trigger="interval",
            seconds=settings.reminder_delivery_interval_seconds,
            id="resume_broadcasts",
            name="Resume Broadcast Deliveries",
            replace_existing=True,
            kwargs={
                "registry": self.registry,
                "bot": self.bot,
                "session_maker": self.session_maker,
                "batch_size": settings.scheduler_batch_size,
            },
        )
        self._scheduler.add_job(
            dispatch_telegram_outbox,
            trigger="interval",
            # Interactive booking screens must not wait for the reminder cycle.
            seconds=settings.telegram_outbox_poll_interval_seconds,
            id="dispatch_telegram_outbox",
            name="Dispatch Telegram Outbox",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
            kwargs={
                "registry": self.registry,
                "session_maker": self.session_maker,
                "batch_size": settings.scheduler_batch_size,
                "max_attempts": settings.job_max_attempts,
                "lease_seconds": settings.job_processing_timeout_seconds,
            },
        )

        if settings.payment_provider.lower() == "yookassa_web":
            self._scheduler.add_job(
                reconcile_pending_yookassa,
                trigger="interval",
                seconds=settings.yookassa_reconciliation_interval_seconds,
                id="reconcile_pending_yookassa",
                name="Reconcile Pending YooKassa Payments",
                replace_existing=True,
                kwargs={
                    "session_maker": self.session_maker,
                    "batch_size": settings.scheduler_batch_size,
                },
            )

        logger.info(
            "MultiTenantScheduler configured with hold_cleaner (%ss), reminder_generator (%ss), reminder_worker (%ss), subscription_worker (600s)",
            settings.hold_cleaner_interval_seconds,
            settings.reminder_generation_interval_seconds,
            settings.reminder_delivery_interval_seconds,
        )

    @property
    def running(self) -> bool:
        """Return True if scheduler is active."""
        if getattr(self, "_is_shutdown", False):
            return False
        return self._scheduler.running

    def start(self) -> None:
        """Start the background scheduler."""
        self._is_shutdown = False
        if not self._scheduler.running:
            self._scheduler.start()
            logger.info("MultiTenantScheduler started")

    def shutdown(self, wait: bool = False) -> None:
        """Shutdown the background scheduler."""
        self._is_shutdown = True
        if self._scheduler.running:
            self._scheduler.shutdown(wait=wait)
            logger.info("MultiTenantScheduler stopped")

    def add_job(self, *args, **kwargs):
        """Pass-through for custom job registration."""
        return self._scheduler.add_job(*args, **kwargs)


def setup_scheduler(
    bot: Optional[Bot] = None,
    registry: Optional[BotRegistry] = None,
    session_maker: async_sessionmaker = async_session_maker,
) -> MultiTenantScheduler:
    """Initialize and configure MultiTenantScheduler with periodic background jobs."""
    return MultiTenantScheduler(
        registry=registry,
        bot=bot,
        session_maker=session_maker,
    )
