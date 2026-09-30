"""
Admin role filter for restricting access to administrative routes.
"""

from typing import Union
from aiogram.filters import Filter
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.settings import settings
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

        # Fast-path check from settings.admin_ids
        if user.id in settings.admin_ids:
            return True

        # Database check in admins table
        user_repo = UserRepository(session)
        return await user_repo.is_admin(user.id)
