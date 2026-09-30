"""
User repository for client management and CRM queries.
"""

from decimal import Decimal
from typing import Any, Dict, Optional, Sequence, Tuple
from sqlalchemy import BigInteger, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database.models.appointment import Appointment, AppointmentStatus
from app.database.models.user import Admin, User, UserMarketingPreference
from app.repositories.base import BaseRepository


class UserRepository(BaseRepository[User]):
    """
    Repository for managing User entities and calculating CRM metrics.
    """

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(User, session)

    async def get_by_telegram_id(self, telegram_id: int) -> Optional[User]:
        """
        Retrieve user by unique Telegram ID.
        """
        query = (
            select(User)
            .where(User.telegram_id == telegram_id)
            .options(
                selectinload(User.admin_profile),
                selectinload(User.marketing_preference),
            )
        )
        result = await self.session.execute(query)
        return result.scalars().first()

    async def get_by_phone_exact(self, phone: str) -> Optional[User]:
        """Reuse a manual client only when the phone has one exact owner."""
        result = await self.session.execute(select(User).where(User.phone == phone).limit(2))
        matches = result.scalars().all()
        return matches[0] if len(matches) == 1 else None

    async def get_or_create(
        self,
        telegram_id: int,
        first_name: str,
        last_name: Optional[str] = None,
        username: Optional[str] = None,
    ) -> Tuple[User, bool]:
        """
        Find existing user or create a new user profile with marketing preferences.
        """
        user = await self.get_by_telegram_id(telegram_id)
        if user:
            # Update names/username and last_activity if changed
            updated = False
            if user.first_name != first_name:
                user.first_name = first_name
                updated = True
            if user.last_name != last_name:
                user.last_name = last_name
                updated = True
            if user.username != username:
                user.username = username
                updated = True
            if user.is_bot_blocked:
                user.is_bot_blocked = False
                updated = True
            if updated:
                await self.session.flush()
            return user, False

        # Create new user
        user = User(
            telegram_id=telegram_id,
            first_name=first_name,
            last_name=last_name,
            username=username,
        )
        self.session.add(user)
        await self.session.flush()

        # Create default marketing preferences
        pref = UserMarketingPreference(user_id=user.id, is_marketing_allowed=True)
        self.session.add(pref)
        await self.session.flush()

        await self.session.refresh(user)
        return user, True

    async def update_phone(self, user_id: int, phone: str) -> Optional[User]:
        """
        Update user's contact phone number.
        """
        return await self.update(user_id, phone=phone)

    async def update_last_activity(self, user_id: int) -> None:
        """
        Update last activity timestamp.
        """
        stmt = (
            update(User)
            .where(User.id == user_id)
            .values(last_activity_at=func.now())
        )
        await self.session.execute(stmt)
        await self.session.flush()

    async def set_bot_blocked(self, telegram_id: int, is_blocked: bool) -> None:
        """
        Mark user as having blocked or unblocked the bot.
        """
        stmt = (
            update(User)
            .where(User.telegram_id == telegram_id)
            .values(is_bot_blocked=is_blocked)
        )
        await self.session.execute(stmt)
        await self.session.flush()

    async def is_admin(self, telegram_id: int) -> bool:
        """
        Check if a given Telegram ID has an active admin role.
        """
        query = (
            select(Admin.id)
            .join(User, Admin.user_id == User.id)
            .where(User.telegram_id == telegram_id, Admin.is_active.is_(True))
        )
        result = await self.session.execute(query)
        return result.scalars().first() is not None

    async def get_active_admin_by_user_id(self, user_id: int) -> Optional[Admin]:
        """Resolve the admins.id identity used by all administrative foreign keys."""
        result = await self.session.execute(
            select(Admin).where(Admin.user_id == user_id, Admin.is_active.is_(True))
        )
        return result.scalars().first()

    async def search_users(self, search_text: str, limit: int = 20) -> Sequence[User]:
        """
        Search users by name, username or phone.
        """
        pattern = f"%{search_text.strip()}%"
        query = (
            select(User)
            .where(
                or_(
                    User.first_name.ilike(pattern),
                    User.last_name.ilike(pattern),
                    User.username.ilike(pattern),
                    User.phone.ilike(pattern),
                )
            )
            .order_by(User.last_activity_at.desc())
            .limit(limit)
        )
        result = await self.session.execute(query)
        return result.scalars().all()

    async def get_user_crm_stats(self, user_id: int) -> Dict[str, Any]:
        """
        Calculate CRM statistics for a user: total bookings, completed, cancelled, no-show, LTV.
        """
        query = (
            select(
                func.count(Appointment.id).label("total_bookings"),
                func.count().filter(Appointment.status == AppointmentStatus.COMPLETED).label("completed"),
                func.count().filter(
                    Appointment.status.in_([
                        AppointmentStatus.CANCELLED_BY_CLIENT,
                        AppointmentStatus.CANCELLED_BY_ADMIN,
                    ])
                ).label("cancelled"),
                func.count().filter(Appointment.status == AppointmentStatus.NO_SHOW).label("no_show"),
                func.coalesce(
                    func.sum(Appointment.snapshot_service_price).filter(
                        Appointment.status == AppointmentStatus.COMPLETED
                    ),
                    Decimal("0.00"),
                ).label("total_spent"),
                func.min(Appointment.start_time).label("first_booking_at"),
                func.max(Appointment.start_time).label("last_booking_at"),
            )
            .where(Appointment.user_id == user_id)
        )
        result = await self.session.execute(query)
        row = result.one()
        return {
            "total_bookings": row.total_bookings or 0,
            "completed": row.completed or 0,
            "cancelled": row.cancelled or 0,
            "no_show": row.no_show or 0,
            "total_spent": row.total_spent or Decimal("0.00"),
            "first_booking_at": row.first_booking_at,
            "last_booking_at": row.last_booking_at,
        }
