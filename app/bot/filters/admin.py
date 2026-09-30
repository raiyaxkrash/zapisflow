"""
Admin role filter for restricting access to administrative routes.
"""

from typing import Union
from aiogram.filters import Filter
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.user_repository import UserRepository


class IsAdminFilter(Filter):
    """
    Checks whether the Telegram user has administrative privileges.
    """

    async def __call__(
        self, event: Union[Message, CallbackQuery], session: AsyncSession, **kwargs
    ) -> bool:
        user = event.from_user
        if not user:
            return False

        # Admin actions require an active Admin row; the seed creates these for ADMIN_IDS.
        user_repo = UserRepository(session)
        return await user_repo.is_admin(user.id)
