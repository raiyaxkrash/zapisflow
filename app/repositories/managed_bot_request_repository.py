"""Repository for durable tracking of Managed Bot creation requests."""

from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models.managed_bot_request import (
    ManagedBotCreationRequest,
    ManagedBotRequestStatus,
)


class ManagedBotRequestRepository:
    """Repository handling persistence and lifecycle of Managed Bot creation requests."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create_or_renew_request(
        self,
        owner_user_id: int,
        telegram_owner_user_id: int,
        master_id: int,
        suggested_name: Optional[str] = None,
        suggested_username: Optional[str] = None,
        request_id: int = 1,
        ttl_hours: int = 2,
    ) -> ManagedBotCreationRequest:
        """Expire any existing PENDING request for this user and create a single durable pending request."""
        now = datetime.now(timezone.utc)
        expires_at = now + timedelta(hours=ttl_hours)

        # Expire older pending requests for this telegram user to satisfy partial unique index
        await self.session.execute(
            update(ManagedBotCreationRequest)
            .where(
                ManagedBotCreationRequest.telegram_owner_user_id == telegram_owner_user_id,
                ManagedBotCreationRequest.status == ManagedBotRequestStatus.PENDING,
            )
            .values(status=ManagedBotRequestStatus.EXPIRED)
        )

        req = ManagedBotCreationRequest(
            owner_user_id=owner_user_id,
            telegram_owner_user_id=telegram_owner_user_id,
            master_id=master_id,
            request_id=request_id,
            suggested_name=suggested_name,
            suggested_username=suggested_username,
            status=ManagedBotRequestStatus.PENDING,
            created_at=now,
            expires_at=expires_at,
        )
        self.session.add(req)
        await self.session.flush()
        return req

    async def get_active_pending_request(
        self,
        telegram_owner_user_id: int,
        for_update: bool = False,
    ) -> Optional[ManagedBotCreationRequest]:
        """Fetch the single valid PENDING creation request for a Telegram user, handling expiration."""
        now = datetime.now(timezone.utc)
        stmt = select(ManagedBotCreationRequest).where(
            ManagedBotCreationRequest.telegram_owner_user_id == telegram_owner_user_id,
            ManagedBotCreationRequest.status == ManagedBotRequestStatus.PENDING,
        )
        if for_update:
            stmt = stmt.with_for_update()

        req = await self.session.scalar(stmt)
        if not req:
            return None

        # Check expiration
        if req.expires_at < now:
            req.status = ManagedBotRequestStatus.EXPIRED
            await self.session.flush()
            return None

        return req

    async def complete_request(
        self,
        request_id: int,
        telegram_bot_id: int,
    ) -> Optional[ManagedBotCreationRequest]:
        """Mark creation request as COMPLETED and attach created telegram_bot_id."""
        req = await self.session.get(ManagedBotCreationRequest, request_id)
        if req:
            req.status = ManagedBotRequestStatus.COMPLETED
            req.telegram_bot_id = telegram_bot_id
            await self.session.flush()
        return req

    async def fail_request(self, request_id: int) -> Optional[ManagedBotCreationRequest]:
        """Mark creation request as FAILED."""
        req = await self.session.get(ManagedBotCreationRequest, request_id)
        if req:
            req.status = ManagedBotRequestStatus.FAILED
            await self.session.flush()
        return req
