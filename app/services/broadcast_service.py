"""
Broadcast engine service for marketing and informational mass mailings.
Implements safe rate-limiting, opt-out filtering, error handling and status tracking.
"""

import asyncio
import logging
from typing import List, Optional
from aiogram import Bot
from aiogram.exceptions import TelegramForbiddenError, TelegramRetryAfter
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiolimiter import AsyncLimiter
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

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
                | (UserMarketingPreference.id.is_(None)),
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
        Send broadcast to all recipients with 25 msg/sec rate-limiting.
        """
        query = select(Broadcast).where(Broadcast.id == broadcast_id)
        res = await self.session.execute(query)
        broadcast = res.scalars().first()
        if not broadcast:
            raise ValueError(f"Broadcast #{broadcast_id} not found")

        broadcast.status = BroadcastStatus.SENDING
        broadcast.started_at = func.now()
        await self.session.flush()

        users = await self.get_eligible_users()
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

        success = 0
        failed = 0

        for user in users:
            async with limiter:
                try:
                    if broadcast.photo_file_id:
                        await bot.send_photo(
                            chat_id=user.telegram_id,
                            photo=broadcast.photo_file_id,
                            caption=broadcast.text,
                            reply_markup=reply_markup,
                        )
                    else:
                        await bot.send_message(
                            chat_id=user.telegram_id,
                            text=broadcast.text,
                            reply_markup=reply_markup,
                        )

                    rec = BroadcastRecipient(
                        broadcast_id=broadcast.id,
                        user_id=user.id,
                        status=RecipientStatus.SENT,
                        sent_at=func.now(),
                    )
                    self.session.add(rec)
                    success += 1
                except TelegramForbiddenError:
                    # User blocked the bot
                    user.is_bot_blocked = True
                    rec = BroadcastRecipient(
                        broadcast_id=broadcast.id,
                        user_id=user.id,
                        status=RecipientStatus.FAILED,
                        error_message="Бот заблокирован пользователем",
                    )
                    self.session.add(rec)
                    failed += 1
                except TelegramRetryAfter as e:
                    await asyncio.sleep(e.retry_after + 1)
                    try:
                        if broadcast.photo_file_id:
                            await bot.send_photo(
                                chat_id=user.telegram_id,
                                photo=broadcast.photo_file_id,
                                caption=broadcast.text,
                                reply_markup=reply_markup,
                            )
                        else:
                            await bot.send_message(
                                chat_id=user.telegram_id,
                                text=broadcast.text,
                                reply_markup=reply_markup,
                            )
                        rec = BroadcastRecipient(
                            broadcast_id=broadcast.id,
                            user_id=user.id,
                            status=RecipientStatus.SENT,
                            sent_at=func.now(),
                        )
                        self.session.add(rec)
                        success += 1
                    except Exception as err:
                        rec = BroadcastRecipient(
                            broadcast_id=broadcast.id,
                            user_id=user.id,
                            status=RecipientStatus.FAILED,
                            error_message=str(err)[:250],
                        )
                        self.session.add(rec)
                        failed += 1
                except Exception as exc:
                    rec = BroadcastRecipient(
                        broadcast_id=broadcast.id,
                        user_id=user.id,
                        status=RecipientStatus.FAILED,
                        error_message=str(exc)[:250],
                    )
                    self.session.add(rec)
                    failed += 1

                # Flush periodically
                if (success + failed) % 30 == 0:
                    broadcast.success_count = success
                    broadcast.fail_count = failed
                    await self.session.flush()

        broadcast.status = BroadcastStatus.COMPLETED
        broadcast.success_count = success
        broadcast.fail_count = failed
        broadcast.finished_at = func.now()
        await self.session.flush()
        await self.session.refresh(broadcast)

        logger.info(
            f"Broadcast #{broadcast.id} completed: {success} sent, {failed} failed."
        )
        return broadcast
