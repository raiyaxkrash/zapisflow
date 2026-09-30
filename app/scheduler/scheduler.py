"""
APScheduler configuration and background task runner.
Manages periodic jobs for hold expiration cleaning and client visit reminders.
"""

import logging
from aiogram import Bot
from apscheduler.schedulers.asyncio import AsyncIOScheduler
import pytz

from app.config.settings import settings
from app.scheduler.jobs.hold_cleaner import clean_expired_holds
from app.scheduler.jobs.reminder_worker import send_visit_reminders

logger = logging.getLogger("app.scheduler")


def setup_scheduler(bot: Bot) -> AsyncIOScheduler:
    """
    Initialize and configure AsyncIOScheduler with periodic background jobs.
    """
    scheduler = AsyncIOScheduler(timezone=pytz.timezone(settings.timezone))

    # 1. Hold cleaner job: runs every 60 seconds
    scheduler.add_job(
        clean_expired_holds,
        trigger="interval",
        seconds=60,
        id="clean_expired_holds",
        name="Clean Expired Holds",
        replace_existing=True,
        kwargs={"bot": bot},
    )

    # 2. Visit reminders job: runs every 120 seconds
    scheduler.add_job(
        send_visit_reminders,
        trigger="interval",
        seconds=120,
        id="send_visit_reminders",
        name="Send Visit Reminders",
        replace_existing=True,
        kwargs={"bot": bot},
    )

    logger.info("APScheduler initialized with hold cleaner (60s) and visit reminders (120s)")
    return scheduler
