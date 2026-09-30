"""
User context middleware for automatic user registration and tracking in database.
"""

from typing import Any, Awaitable, Callable, Dict
from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, User as TgUser

from app.config.settings import settings
from app.repositories.user_repository import UserRepository


class UserContextMiddleware(BaseMiddleware):
    """
    Middleware ensuring the Telegram user is stored in the database and injected into handler data.
    """

    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        tg_user: TgUser | None = data.get("event_from_user")
        session = data.get("session")

        if tg_user and session:
            user_repo = UserRepository(session)
            db_user, is_new = await user_repo.get_or_create(
                telegram_id=tg_user.id,
                first_name=tg_user.first_name,
                last_name=tg_user.last_name,
                username=tg_user.username,
            )
            data["db_user"] = db_user
            data["is_admin"] = (tg_user.id in settings.admin_ids) or await user_repo.is_admin(tg_user.id)
            # Commit user registration independently of the handler's business transaction
            await session.commit()
        else:
            data["db_user"] = None
            data["is_admin"] = False

        return await handler(event, data)
