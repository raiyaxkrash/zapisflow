"""
Subscription notification service for sending lifecycle reminders to SaaS master owners.
Covers: 3-day notice, 1-day notice, last day, day after expiration (expired), and payment success.
Uses deduplication via idempotency keys to guarantee at-most-once delivery per reminder stage.
"""

from datetime import datetime, timedelta, timezone
from html import escape
import logging
from typing import Optional

from aiogram import Bot
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.settings import settings
from app.database.models.audit import AuditLog
from app.database.models.master import Master, SubscriptionStatus
from app.database.models.subscription import EffectiveSubscriptionStatus, SubscriptionPayment, SubscriptionPlan
from app.database.models.user import User
from app.services.subscription_service import SubscriptionService

logger = logging.getLogger("app.services.subscription_notifications")


class SubscriptionNotificationService:
    """Manages transactional and reminder notifications for master subscriptions."""

    def __init__(self, session: AsyncSession, bot: Optional[Bot] = None) -> None:
        self.session = session
        self.bot = bot
        self.sub_service = SubscriptionService(session)

    async def get_or_create_bot(self) -> Optional[Bot]:
        """Resolve bot instance for Manager Bot."""
        if self.bot is not None:
            return self.bot
        if settings.manager_bot_token and settings.manager_bot_token != "dummy_token_for_init":
            return Bot(token=settings.manager_bot_token)
        return None

    async def send_payment_success_notification(
        self,
        master_id: int,
        payment_id: int,
    ) -> bool:
        """
        Send confirmation notification to project owner after successful subscription payment.
        """
        master = await self.session.get(Master, master_id)
        if not master:
            return False

        owner = await self.session.get(User, master.owner_user_id)
        if not owner or not owner.telegram_id:
            return False

        payment = await self.session.get(SubscriptionPayment, payment_id)
        plan_name = "ZapisFlow Basic"
        if payment and payment.plan_id:
            plan = await self.session.get(SubscriptionPlan, payment.plan_id)
            if plan:
                plan_name = plan.name

        paid_str = master.paid_until.strftime("%d.%m.%Y") if master.paid_until else "—"

        text = (
            "🎉 <b>Оплата прошла успешно!</b>\n\n"
            f"Подписка <b>{escape(plan_name)}</b> для проекта «{escape(master.display_name)}» продлена до <b>{paid_str}</b>.\n\n"
            "Спасибо, что вы с нами!"
        )

        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="💳 Моя подписка", callback_data=f"mgr:sub:{master.id}")],
                [InlineKeyboardButton(text=f"💬 Поддержка @{settings.support_telegram_username}", url=settings.support_url)],
            ]
        )

        # Audit dedup
        idempotency_key = f"sub_notice:success:{payment_id}"
        existing_log = await self.session.scalar(
            select(AuditLog.id).where(
                AuditLog.master_id == master_id,
                AuditLog.action == "SUBSCRIPTION_NOTICE_PAYMENT_SUCCESS",
                AuditLog.entity_id == payment_id,
            )
        )
        if existing_log:
            return True

        bot = await self.get_or_create_bot()
        if bot:
            try:
                await bot.send_message(
                    chat_id=owner.telegram_id,
                    text=text,
                    reply_markup=keyboard,
                    parse_mode="HTML",
                )
            except Exception as exc:
                logger.warning("Failed to send payment success notification to user #%s: %s", owner.id, exc)

        audit = AuditLog(
            master_id=master_id,
            actor_user_id=owner.id,
            action="SUBSCRIPTION_NOTICE_PAYMENT_SUCCESS",
            entity_type="SubscriptionPayment",
            entity_id=payment_id,
            payload_after={"idempotency_key": idempotency_key, "paid_until": paid_str},
        )
        self.session.add(audit)
        await self.session.flush()
        return True

    async def check_and_send_expiring_reminders(
        self,
        now_utc: Optional[datetime] = None,
    ) -> int:
        """
        Scan all active masters and send lifecycle reminders:
        - 3 days before expiry
        - 1 day before expiry
        - On the day of expiry (today)
        - Day after expiry (expired)
        """
        if now_utc is None:
            now_utc = datetime.now(timezone.utc)
        elif now_utc.tzinfo is None:
            now_utc = now_utc.replace(tzinfo=timezone.utc)

        # Fetch masters that are not suspended
        stmt = (
            select(Master)
            .where(Master.subscription_status != SubscriptionStatus.SUSPENDED)
        )
        res = await self.session.execute(stmt)
        masters = list(res.scalars().all())

        sent_count = 0
        plan = None
        try:
            plan = await self.sub_service.get_active_plan()
        except Exception:
            pass
        plan_name = plan.name if plan else "ZapisFlow Basic"
        price_str = f"{int(plan.price_rub):,} ₽".replace(",", " ") if plan else "499 ₽"

        for m in masters:
            eff = await self.sub_service.get_effective_status(m.id, now_utc=now_utc)
            if eff.status == EffectiveSubscriptionStatus.SUSPENDED:
                continue

            owner = await self.session.get(User, m.owner_user_id)
            if not owner or not owner.telegram_id:
                continue

            target_stage = None
            msg_text = None
            btn_text = f"💳 Продлить ({price_str})"

            expiry_dt = eff.expires_at
            if not expiry_dt:
                continue

            delta = expiry_dt - now_utc
            days_left = delta.days
            if delta.total_seconds() > 0 and delta.seconds > 0:
                days_left += 1

            if eff.status in (EffectiveSubscriptionStatus.TRIAL_ACTIVE, EffectiveSubscriptionStatus.PAID_ACTIVE):
                if days_left == 3:
                    target_stage = "expiring_3d"
                    msg_text = (
                        f"⚠️ До окончания подписки <b>{escape(plan_name)}</b> для проекта «{escape(m.display_name)}» осталось <b>3 дня</b>.\n\n"
                        "Продлите подписку, чтобы не прерывать онлайн-запись клиентов."
                    )
                elif days_left == 1:
                    target_stage = "expiring_1d"
                    msg_text = (
                        f"⏰ Подписка <b>{escape(plan_name)}</b> для проекта «{escape(m.display_name)}» истекает <b>завтра</b>!\n\n"
                        "После окончания приём новых записей будет приостановлен."
                    )
                elif days_left == 0 or (0 < delta.total_seconds() <= 86400):
                    target_stage = "expiring_today"
                    msg_text = (
                        f"🔴 Сегодня последний день подписки <b>{escape(plan_name)}</b> для проекта «{escape(m.display_name)}».\n\n"
                        "Продлите прямо сейчас, чтобы онлайн-запись не остановилась!"
                    )
            elif eff.status == EffectiveSubscriptionStatus.EXPIRED:
                # Expired within last 24h
                seconds_expired = abs(delta.total_seconds())
                if seconds_expired <= 86400 * 2:
                    target_stage = "expired_yesterday"
                    btn_text = f"💳 Возобновить подписку ({price_str})"
                    msg_text = (
                        f"❌ Подписка <b>{escape(plan_name)}</b> для проекта «{escape(m.display_name)}» истекла.\n\n"
                        "Онлайн-запись клиентов приостановлена.\n"
                        "Все ваши данные и расписание в безопасности!"
                    )

            if not target_stage or not msg_text:
                continue

            date_key = expiry_dt.strftime("%Y-%m-%d")
            action_key = f"SUBSCRIPTION_REMINDER_{target_stage.upper()}"

            # Deduplication check in AuditLog
            audit_exists = await self.session.scalar(
                select(AuditLog.id).where(
                    AuditLog.master_id == m.id,
                    AuditLog.action == action_key,
                    AuditLog.created_at >= now_utc - timedelta(days=2),
                )
            )
            if audit_exists:
                continue

            # Send message
            keyboard = InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text=btn_text, callback_data=f"mgr:sub:{m.id}")],
                    [InlineKeyboardButton(text=f"💬 Поддержка @{settings.support_telegram_username}", url=settings.support_url)],
                ]
            )

            bot = await self.get_or_create_bot()
            if bot:
                try:
                    await bot.send_message(
                        chat_id=owner.telegram_id,
                        text=msg_text,
                        reply_markup=keyboard,
                        parse_mode="HTML",
                    )
                except Exception as exc:
                    logger.warning("Failed to send subscription reminder to user #%s: %s", owner.id, exc)

            # Record audit
            audit = AuditLog(
                master_id=m.id,
                actor_user_id=owner.id,
                action=action_key,
                entity_type="Master",
                entity_id=m.id,
                payload_after={"stage": target_stage, "days_left": days_left, "expiry_date": date_key},
            )
            self.session.add(audit)
            sent_count += 1

        if sent_count > 0:
            await self.session.flush()
            logger.info("Sent %d subscription lifecycle reminders", sent_count)

        return sent_count
