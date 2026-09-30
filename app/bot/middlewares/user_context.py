"""User context middleware for automatic user registration and tenant-aware authorization (Phase 4).

Enriches handler data with:
- current_user / db_user
- master_id
- admin_role
- is_admin
- is_owner
"""

from typing import Any, Awaitable, Callable, Dict
from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, User as TgUser

from app.config.settings import settings
from app.repositories.user_repository import UserRepository
from app.services.master_authorization_service import AdminRole, MasterAuthorizationService
from app.services.tenant_context import LegacyTenantResolver


class UserContextMiddleware(BaseMiddleware):
    """Middleware ensuring the Telegram user is registered and injecting tenant-aware authorization data."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        tg_user: TgUser | None = data.get("event_from_user")
        session = data.get("session")

        if tg_user and session:
            # 1. Resolve or register user
            user_repo = UserRepository(session)
            db_user, is_new = await user_repo.get_or_create(
                telegram_id=tg_user.id,
                first_name=tg_user.first_name,
                last_name=tg_user.last_name,
                username=tg_user.username,
            )

            # 2. Resolve current master_id
            master_id = data.get("master_id")
            if master_id is None:
                if settings.app_mode == "polling":
                    master_id = await LegacyTenantResolver.get_master_id(session)
                else:
                    return None  # Fail closed in webhook mode
            data["master_id"] = master_id

            # 3. Dynamic tenant authorization
            auth_service = MasterAuthorizationService(session)
            role = await auth_service.get_role(master_id=master_id, user_id=db_user.id)

            data["db_user"] = db_user
            data["current_user"] = db_user
            data["admin_role"] = role
            data["is_admin"] = role in (AdminRole.OWNER, AdminRole.ADMIN)
            data["is_owner"] = role == AdminRole.OWNER

            # Commit user registration independently of handler transaction
            await session.commit()
        else:
            data["db_user"] = None
            data["current_user"] = None
            data["master_id"] = None
            data["admin_role"] = AdminRole.NONE
            data["is_admin"] = False
            data["is_owner"] = False

        return await handler(event, data)
