"""Bot factory for creating and configuring aiogram.Bot runtime instances."""

from typing import Optional
from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.enums import ParseMode


class BotFactory:
    """Factory creating configured aiogram.Bot instances without side-effects."""

    @staticmethod
    def create(token: str, session: Optional[AiohttpSession] = None) -> Bot:
        """Create an aiogram.Bot instance with standard HTML parse mode.

        Accepts optional AiohttpSession. Does not make network requests (e.g. getMe) upon creation.
        """
        return Bot(
            token=token,
            session=session,
            default=DefaultBotProperties(parse_mode=ParseMode.HTML),
        )
