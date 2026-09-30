"""Admin role filter for restricting access to tenant administrative routes (Phase 4).

Checks whether the Telegram user has administrative privileges for the current master
via MasterAuthorizationService.
"""

from typing import Optional, Union
from aiogram.filters import Filter
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.settings import settings
from app.database.models.user import User
from app.repositories.user_repository import UserRepository
from app.services.master_authorization_service import MasterAuthorizationService
from app.services.tenant_context import LegacyTenantResolver


class IsAdminFilter(Filter):
    """Checks whether the user has administrative privileges for the current master."""

    async def __call__(
        self,
        event: Union[Message, CallbackQuery],
        session: AsyncSession,
        master_id: Optional[int] = None,
        db_user: Optional[User] = None,
        **kwargs,
    ) -> bool:
        tg_user = event.from_user
        if not tg_user:
            return False

        if master_id is None:
            if settings.app_mode == "polling":
                master_id = await LegacyTenantResolver.get_master_id(session)
            else:
                return False

        if db_user is None:
            user_repo = UserRepository(session)
            db_user = await user_repo.get_by_telegram_id(tg_user.id)
            if not db_user:
                return False

        auth_service = MasterAuthorizationService(session)
        return await auth_service.is_admin(master_id=master_id, user_id=db_user.id)
