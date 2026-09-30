"""
Broadcast engine service for marketing and informational mass mailings.
Implements safe rate-limiting, opt-out filtering, error handling and status tracking.
"""

import asyncio
import logging
from datetime import datetime, timezone
from typing import List, Optional
from aiogram import Bot
from aiogram.exceptions import TelegramForbiddenError, TelegramRetryAfter
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiolimiter import AsyncLimiter
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models.broadcast import (
    Broadcast,
    BroadcastRecipient,
    BroadcastStatus,
    RecipientStatus,
)
from app.database.models.user import User, UserMarketingPreference

logger = logging.getLogger("app.broadcast")


class BroadcastService:
    """
    Mass messaging service with rate limiting and recipient status tracking.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_eligible_users(self) -> List[User]:
        """
        Get all active users who have not blocked the bot and opted in to marketing.
        """
        query = (
            select(User)
            .join(UserMarketingPreference, User.id == UserMarketingPreference.user_id, isouter=True)
            .where(
                User.telegram_id > 0,
                User.is_bot_blocked.is_(False),
                (UserMarketingPreference.is_marketing_allowed.is_(True))
                | (UserMarketingPreference.user_id.is_(None)),
            )
        )
        res = await self.session.execute(query)
        return list(res.scalars().all())

    async def create_broadcast(
        self,
        text: str,
        admin_id: Optional[int] = None,
        photo_file_id: Optional[str] = None,
        button_text: Optional[str] = None,
        button_url: Optional[str] = None,
    ) -> Broadcast:
        """
        Create a new broadcast campaign in DRAFT status and calculate total recipients.
        """
        eligible = await self.get_eligible_users()
        broadcast = Broadcast(
            admin_id=admin_id,
            text=text,
            photo_file_id=photo_file_id,
            button_text=button_text,
            button_url=button_url,
            status=BroadcastStatus.DRAFT,
            total_count=len(eligible),
            success_count=0,
            fail_count=0,
        )
        self.session.add(broadcast)
        await self.session.flush()
        await self.session.refresh(broadcast)
        return broadcast

    async def execute_broadcast(
        self, broadcast_id: int, bot: Bot
    ) -> Broadcast:
        """
        Send a campaign without holding database locks during Telegram calls.

        A campaign can start only from DRAFT. A repeated callback sees SENDING
        or COMPLETED and cannot dispatch the same campaign again. Each outcome
        is committed separately; a process interruption leaves SENDING for
        operator review instead of automatically risking duplicate delivery.
        """
        query = select(Broadcast).where(Broadcast.id == broadcast_id)
        res = await self.session.execute(query)
        broadcast = res.scalars().first()
        if not broadcast:
            raise ValueError(f"Broadcast #{broadcast_id} not found")
        if broadcast.status != BroadcastStatus.DRAFT:
            await self.session.commit()
            return broadcast

        users = await self.get_eligible_users()
        recipient_ids = [(user.id, user.telegram_id) for user in users]
        photo_file_id = broadcast.photo_file_id
        message_text = broadcast.text
        broadcast.status = BroadcastStatus.SENDING
        broadcast.started_at = datetime.now(timezone.utc)
        broadcast.total_count = len(recipient_ids)
        await self.session.commit()

        limiter = AsyncLimiter(max_rate=25, time_period=1.0)

        # Inline button if configured
        reply_markup = None
        if broadcast.button_text and broadcast.button_url:
            reply_markup = InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text=broadcast.button_text,
                            url=broadcast.button_url,
                        )
                    ]
                ]
            )

        success = broadcast.success_count
        failed = broadcast.fail_count

        for user_id, telegram_id in recipient_ids:
            async with limiter:
                outcome = RecipientStatus.FAILED
                error_message = None
                blocked = False
                try:
                    if photo_file_id:
                        await bot.send_photo(
                            chat_id=telegram_id,
                            photo=photo_file_id,
                            caption=message_text,
                            reply_markup=reply_markup,
                        )
                    else:
                        await bot.send_message(
                            chat_id=telegram_id,
                            text=message_text,
                            reply_markup=reply_markup,
                        )
                    outcome = RecipientStatus.SENT
                except TelegramForbiddenError:
                    blocked = True
                    error_message = "Бот заблокирован пользователем"
                except TelegramRetryAfter as e:
                    await asyncio.sleep(e.retry_after + 1)
                    try:
                        if photo_file_id:
                            await bot.send_photo(
                                chat_id=telegram_id,
                                photo=photo_file_id,
                                caption=message_text,
                                reply_markup=reply_markup,
                            )
                        else:
                            await bot.send_message(
                                chat_id=telegram_id,
                                text=message_text,
                                reply_markup=reply_markup,
                            )
                        outcome = RecipientStatus.SENT
                    except Exception as err:
                        error_message = str(err)[:250]
                except Exception as exc:
                    error_message = str(exc)[:250]

                if outcome == RecipientStatus.SENT:
                    success += 1
                else:
                    failed += 1
                async with self.session.begin():
                    if blocked:
                        user = await self.session.get(User, user_id)
                        if user is not None:
                            user.is_bot_blocked = True
                    self.session.add(
                        BroadcastRecipient(
                            broadcast_id=broadcast.id,
                            user_id=user_id,
                            status=outcome,
                            error_message=error_message,
                            sent_at=datetime.now(timezone.utc)
                            if outcome == RecipientStatus.SENT
                            else None,
                        )
                    )
                    broadcast.success_count = success
                    broadcast.fail_count = failed

        async with self.session.begin():
            broadcast.status = BroadcastStatus.COMPLETED
            broadcast.finished_at = datetime.now(timezone.utc)

        logger.info(
            f"Broadcast #{broadcast.id} completed: {success} sent, {failed} failed."
        )
        return broadcast
