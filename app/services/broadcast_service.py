"""Broadcast engine service for marketing and informational mass mailings.

Implements strict tenant recipient filtering via master_clients, safe rate-limiting,
and per-master status tracking.
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
from app.database.models.master import MasterClient
from app.database.models.user import User

logger = logging.getLogger("app.broadcast")


class BroadcastService:
    """Mass messaging service with tenant recipient isolation and status tracking."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_eligible_users(self, master_id: int) -> List[User]:
        """Get active users who have opted into marketing specifically for this master.
        
        Reads exclusively from master_clients where master_id = master_id
        and is_marketing_allowed = TRUE and is_bot_blocked = FALSE.
        """
        query = (
            select(User)
            .join(MasterClient, MasterClient.user_id == User.id)
            .where(
                MasterClient.master_id == master_id,
                MasterClient.is_marketing_allowed.is_(True),
                MasterClient.is_bot_blocked.is_(False),
                User.telegram_id > 0,
            )
            .order_by(User.id.asc())
        )
        res = await self.session.execute(query)
        return list(res.scalars().all())

    async def count_recipients(self, master_id: int) -> int:
        """Count active users opted into marketing for this master."""
        users = await self.get_eligible_users(master_id)
        return len(users)

    async def create_broadcast(
        self,
        master_id: int,
        text: str,
        admin_id: Optional[int] = None,
        photo_file_id: Optional[str] = None,
        button_text: Optional[str] = None,
        button_url: Optional[str] = None,
    ) -> Broadcast:
        """Create a new broadcast campaign strictly belonging to master_id."""
        eligible = await self.get_eligible_users(master_id)
        broadcast = Broadcast(
            master_id=master_id,
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
        self, master_id: int, broadcast_id: int, bot: Bot
    ) -> Broadcast:
        """Send a campaign ensuring it strictly belongs to master_id."""
        query = select(Broadcast).where(
            Broadcast.id == broadcast_id,
            Broadcast.master_id == master_id,
        )
        res = await self.session.execute(query)
        broadcast = res.scalars().first()
        if not broadcast:
            raise ValueError(f"Broadcast #{broadcast_id} not found for master {master_id}")
        if broadcast.status != BroadcastStatus.DRAFT:
            await self.session.commit()
            return broadcast

        recipients = await self.get_eligible_users(master_id)
        broadcast.total_count = len(recipients)
        broadcast.status = BroadcastStatus.SENDING
        broadcast.started_at = datetime.now(timezone.utc)
        await self.session.commit()

        reply_markup = None
        if broadcast.button_text and broadcast.button_url:
            reply_markup = InlineKeyboardMarkup(
                inline_keyboard=[[
                    InlineKeyboardButton(
                        text=broadcast.button_text,
                        url=broadcast.button_url,
                    )
                ]]
            )

        limiter = AsyncLimiter(25, 1.0)
        success_count = 0
        fail_count = 0

        for user in recipients:
            async with limiter:
                delivered = False
                error_msg = None
                for attempt in range(3):
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
                        delivered = True
                        break
                    except TelegramRetryAfter as exc:
                        logger.warning(
                            "Telegram rate limited broadcast #%s, sleeping %s s",
                            broadcast_id,
                            exc.retry_after,
                        )
                        await asyncio.sleep(exc.retry_after)
                    except TelegramForbiddenError:
                        error_msg = "Bot blocked by user"
                        break
                    except Exception as exc:
                        error_msg = str(exc)
                        break

                async with self.session.begin():
                    # Record per-recipient outcome
                    rcpt = BroadcastRecipient(
                        broadcast_id=broadcast_id,
                        user_id=user.id,
                        status=RecipientStatus.SENT if delivered else RecipientStatus.FAILED,
                        error_message=error_msg,
                        sent_at=datetime.now(timezone.utc) if delivered else None,
                    )
                    self.session.add(rcpt)

                    if not delivered and error_msg == "Bot blocked by user":
                        # Mark bot as blocked for this master
                        mc_res = await self.session.execute(
                            select(MasterClient).where(
                                MasterClient.master_id == master_id,
                                MasterClient.user_id == user.id,
                            )
                        )
                        mc = mc_res.scalars().first()
                        if mc:
                            mc.is_bot_blocked = True

                    if delivered:
                        success_count += 1
                    else:
                        fail_count += 1

        async with self.session.begin():
            refreshed = await self.session.get(Broadcast, broadcast_id)
            if refreshed:
                refreshed.success_count = success_count
                refreshed.fail_count = fail_count
                refreshed.status = BroadcastStatus.COMPLETED
                refreshed.finished_at = datetime.now(timezone.utc)
                broadcast = refreshed

        logger.info(
            "Broadcast #%s completed for master %s: %s sent, %s failed",
            broadcast_id,
            master_id,
            success_count,
            fail_count,
        )
        return broadcast
