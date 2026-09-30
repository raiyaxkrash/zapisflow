"""Repository for BotInstance entities.

Manages tenant bot instances, encrypted tokens, lifecycle statuses and errors.
"""

from typing import Optional, Sequence
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models.master import BotInstance, BotInstanceStatus


class BotInstanceRepository:
    """Repository for querying and managing BotInstance entities."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_by_id(self, bot_instance_id: int) -> Optional[BotInstance]:
        """Fetch bot instance by its primary key ID."""
        return await self.session.get(BotInstance, bot_instance_id)

    async def get_by_telegram_bot_id(self, telegram_bot_id: int) -> Optional[BotInstance]:
        """Fetch bot instance by its Telegram Bot ID."""
        query = select(BotInstance).where(BotInstance.telegram_bot_id == telegram_bot_id)
        result = await self.session.execute(query)
        return result.scalars().first()

    async def get_active_by_master_id(self, master_id: int) -> Optional[BotInstance]:
        """Fetch active bot instance assigned to the master."""
        query = select(BotInstance).where(
            BotInstance.master_id == master_id,
            BotInstance.status == BotInstanceStatus.ACTIVE,
        )
        result = await self.session.execute(query)
        return result.scalars().first()

    async def list_by_master_id(self, master_id: int) -> Sequence[BotInstance]:
        """List all bot instances associated with a master."""
        query = select(BotInstance).where(BotInstance.master_id == master_id).order_by(BotInstance.id.asc())
        result = await self.session.execute(query)
        return result.scalars().all()

    async def list_enabled(self) -> Sequence[BotInstance]:
        """List all bot instances eligible for execution (ACTIVE)."""
        query = select(BotInstance).where(
            BotInstance.status == BotInstanceStatus.ACTIVE
        ).order_by(BotInstance.id.asc())
        result = await self.session.execute(query)
        return result.scalars().all()

    async def update_status(
        self, bot_instance_id: int, status: BotInstanceStatus
    ) -> bool:
        """Update operational status of a bot instance."""
        instance = await self.get_by_id(bot_instance_id)
        if not instance:
            return False
        instance.status = status
        await self.session.flush()
        return True

    async def update_encrypted_token(
        self,
        bot_instance_id: int,
        encrypted_token: str,
        new_token_version: Optional[int] = None,
    ) -> bool:
        """Update ciphertext token and optionally advance token version."""
        instance = await self.get_by_id(bot_instance_id)
        if not instance:
            return False
        instance.encrypted_token = encrypted_token
        if new_token_version is not None:
            instance.token_version = new_token_version
        else:
            instance.token_version += 1
        await self.session.flush()
        return True

    async def increment_token_version(self, bot_instance_id: int) -> int:
        """Advance token rotation version counter and return the new version."""
        instance = await self.get_by_id(bot_instance_id)
        if not instance:
            raise ValueError(f"BotInstance #{bot_instance_id} not found")
        instance.token_version += 1
        await self.session.flush()
        return instance.token_version

    async def set_last_error(self, bot_instance_id: int, error_message: str) -> None:
        """Record last operational or synchronization error message."""
        instance = await self.get_by_id(bot_instance_id)
        if instance:
            instance.last_error = error_message
            await self.session.flush()

    async def clear_last_error(self, bot_instance_id: int) -> None:
        """Clear previous error message on successful operation."""
        instance = await self.get_by_id(bot_instance_id)
        if instance and instance.last_error is not None:
            instance.last_error = None
            await self.session.flush()

    async def create_bot_instance(
        self,
        master_id: int,
        telegram_bot_id: int,
        encrypted_token: str,
        telegram_username: Optional[str] = None,
        telegram_first_name: Optional[str] = None,
        status: BotInstanceStatus = BotInstanceStatus.ACTIVE,
        token_version: int = 1,
    ) -> BotInstance:
        """Create and persist a new BotInstance."""
        instance = BotInstance(
            master_id=master_id,
            telegram_bot_id=telegram_bot_id,
            encrypted_token=encrypted_token,
            telegram_username=telegram_username,
            telegram_first_name=telegram_first_name,
            status=status,
            token_version=token_version,
        )
        self.session.add(instance)
        await self.session.flush()
        await self.session.refresh(instance)
        return instance
