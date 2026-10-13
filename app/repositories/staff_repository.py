"""
Staff repository for managing salon specialists, services binding and crypto-secure invites.
Strictly scoped to master_id.
"""

from datetime import datetime, timedelta, timezone
import hashlib
import secrets
from typing import Any, List, Optional, Sequence
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database.models.master import Master, MasterAdmin, MasterAdminRole
from app.database.models.staff import StaffMember, StaffService
from app.repositories.base import BaseRepository


class StaffRepository(BaseRepository[StaffMember]):
    """Repository for managing staff members with strict tenant isolation."""

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(StaffMember, session)

    async def get_by_id(self, staff_id: int, master_id: int) -> Optional[StaffMember]:
        """Fetch staff member strictly scoped to master_id."""
        query = (
            select(StaffMember)
            .where(
                StaffMember.id == staff_id,
                StaffMember.master_id == master_id,
            )
            .options(
                selectinload(StaffMember.staff_services).selectinload(StaffService.service),
                selectinload(StaffMember.user),
            )
        )
        res = await self.session.execute(query)
        return res.scalars().first()

    async def list_active(self, master_id: int) -> Sequence[StaffMember]:
        """List active staff members for client booking."""
        query = (
            select(StaffMember)
            .where(
                StaffMember.master_id == master_id,
                StaffMember.is_active.is_(True),
            )
            .order_by(StaffMember.sort_order.asc(), StaffMember.id.asc())
        )
        res = await self.session.execute(query)
        return res.scalars().all()

    async def list_all(self, master_id: int) -> Sequence[StaffMember]:
        """List all staff members (including disabled/archived) for manager bot."""
        query = (
            select(StaffMember)
            .where(StaffMember.master_id == master_id)
            .order_by(StaffMember.is_active.desc(), StaffMember.sort_order.asc(), StaffMember.id.asc())
        )
        res = await self.session.execute(query)
        return res.scalars().all()

    async def get_primary_or_default(self, master_id: int) -> Optional[StaffMember]:
        """Retrieve the primary/first active staff member of a master."""
        query = (
            select(StaffMember)
            .where(
                StaffMember.master_id == master_id,
                StaffMember.is_active.is_(True),
            )
            .order_by(StaffMember.sort_order.asc(), StaffMember.id.asc())
            .limit(1)
        )
        res = await self.session.execute(query)
        staff = res.scalars().first()
        if not staff:
            # Fallback to any staff member if none active
            fallback_q = (
                select(StaffMember)
                .where(StaffMember.master_id == master_id)
                .order_by(StaffMember.id.asc())
                .limit(1)
            )
            staff = (await self.session.execute(fallback_q)).scalars().first()
        if not staff:
            # Auto-provision primary staff if none exists for this master
            # Lock only on the creation path; ordinary availability reads
            # should not serialize on the tenant row.
            from app.database.models.master import Master

            master_obj = await self.session.scalar(
                select(Master).where(Master.id == master_id).with_for_update()
            )
            if master_obj is None:
                return None
            # Another session may have inserted the staff member while this
            # session waited for the parent lock.
            staff = (await self.session.execute(fallback_q)).scalars().first()
            if staff is not None:
                return staff
            staff = StaffMember(
                master_id=master_id,
                display_name=master_obj.display_name or "Основной мастер",
                is_active=True,
                sort_order=0,
            )
            self.session.add(staff)
            await self.session.flush()
        return staff

    async def create_staff(
        self,
        master_id: int,
        display_name: str,
        specialization: Optional[str] = None,
        description: Optional[str] = None,
        photo_file_id: Optional[str] = None,
        user_id: Optional[int] = None,
        sort_order: int = 0,
    ) -> StaffMember:
        """Create a new staff member strictly attached to master_id."""
        staff = StaffMember(
            master_id=master_id,
            display_name=display_name.strip(),
            specialization=specialization.strip() if specialization else None,
            description=description.strip() if description else None,
            photo_file_id=photo_file_id,
            user_id=user_id,
            sort_order=sort_order,
            is_active=True,
        )
        self.session.add(staff)
        await self.session.flush()
        await self.session.refresh(staff)
        return staff

    async def update_staff(
        self, staff_id: int, master_id: int, **kwargs: Any
    ) -> Optional[StaffMember]:
        """Update staff member strictly scoped to master_id."""
        staff = await self.get_by_id(staff_id, master_id)
        if not staff:
            return None
        for key, val in kwargs.items():
            if hasattr(staff, key):
                setattr(staff, key, val)
        await self.session.flush()
        await self.session.refresh(staff)
        return staff

    async def archive_staff(self, staff_id: int, master_id: int) -> bool:
        """Safely soft-delete/deactivate staff member to preserve appointment and financial history."""
        staff = await self.get_by_id(staff_id, master_id)
        if not staff:
            return False
        staff.is_active = False
        await self.session.flush()
        return True

    # -------------------------------------------------------------------------
    # Staff Services Binding (Many-to-Many strictly within master_id)
    # -------------------------------------------------------------------------
    async def set_staff_services(
        self, staff_id: int, master_id: int, service_ids: Sequence[int]
    ) -> None:
        """Set the active services offered by this staff member strictly within master_id."""
        # 1. Deactivate or delete old associations
        await self.session.execute(
            update(StaffService)
            .where(
                StaffService.master_id == master_id,
                StaffService.staff_id == staff_id,
            )
            .values(is_active=False)
        )
        # 2. Add or reactivate requested services
        for s_id in service_ids:
            existing = (
                await self.session.execute(
                    select(StaffService).where(
                        StaffService.master_id == master_id,
                        StaffService.staff_id == staff_id,
                        StaffService.service_id == s_id,
                    )
                )
            ).scalars().first()
            if existing:
                existing.is_active = True
            else:
                new_ss = StaffService(
                    master_id=master_id,
                    staff_id=staff_id,
                    service_id=s_id,
                    is_active=True,
                )
                self.session.add(new_ss)
        await self.session.flush()

    async def list_services_for_staff(
        self, staff_id: int, master_id: int
    ) -> Sequence[int]:
        """List active service IDs assigned to a specific staff member."""
        query = select(StaffService.service_id).where(
            StaffService.master_id == master_id,
            StaffService.staff_id == staff_id,
            StaffService.is_active.is_(True),
        )
        res = await self.session.execute(query)
        return res.scalars().all()

    async def list_staff_for_service(
        self, service_id: int, master_id: int
    ) -> Sequence[StaffMember]:
        """List active staff members offering a specific service."""
        query = (
            select(StaffMember)
            .join(StaffService, StaffMember.id == StaffService.staff_id)
            .where(
                StaffMember.master_id == master_id,
                StaffMember.is_active.is_(True),
                StaffService.service_id == service_id,
                StaffService.is_active.is_(True),
            )
            .order_by(StaffMember.sort_order.asc(), StaffMember.id.asc())
        )
        res = await self.session.execute(query)
        return res.scalars().all()

    # -------------------------------------------------------------------------
    # Crypto-Secure Staff Invites
    # -------------------------------------------------------------------------
    async def create_invite_token(
        self, staff_id: int, master_id: int, expires_days: int = 7
    ) -> Optional[str]:
        """
        Generate crypto-secure single-use invite token.
        Only SHA-256 hash is stored in database; raw token returned once.
        """
        staff = await self.get_by_id(staff_id, master_id)
        if not staff:
            return None

        raw_token = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
        expires_at = datetime.now(timezone.utc) + timedelta(days=expires_days)

        staff.invite_token_hash = token_hash
        staff.invite_expires_at = expires_at
        staff.invite_used_at = None
        await self.session.flush()
        return raw_token

    async def claim_invite_token_atomic(
        self, raw_token: str, user_id: int
    ) -> Optional[StaffMember]:
        """
        Atomically claim invite token using single UPDATE ... WHERE RETURNING to prevent race conditions.
        Returns the StaffMember if claimed, or None if expired/invalid/already used.
        """
        if not raw_token or len(raw_token) < 16:
            return None

        token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
        now_utc = datetime.now(timezone.utc)

        # Atomic update with WHERE clause guarantees single execution even under concurrency
        stmt = (
            update(StaffMember)
            .where(
                StaffMember.invite_token_hash == token_hash,
                StaffMember.invite_used_at.is_(None),
                StaffMember.invite_expires_at > now_utc,
            )
            .values(
                invite_used_at=now_utc,
                invite_token_hash=None,  # Invalidate immediately
                user_id=user_id,
            )
            .returning(StaffMember.id, StaffMember.master_id)
        )
        result = await self.session.execute(stmt)
        row = result.first()
        if not row:
            return None

        staff_id, master_id = row

        # Add or update MasterAdmin membership with STAFF role
        admin_entry = (
            await self.session.execute(
                select(MasterAdmin).where(
                    MasterAdmin.master_id == master_id,
                    MasterAdmin.user_id == user_id,
                )
            )
        ).scalars().first()

        if admin_entry:
            admin_entry.role = MasterAdminRole.STAFF
            admin_entry.staff_id = staff_id
            admin_entry.is_active = True
        else:
            new_admin = MasterAdmin(
                master_id=master_id,
                user_id=user_id,
                role=MasterAdminRole.STAFF,
                staff_id=staff_id,
                is_active=True,
            )
            self.session.add(new_admin)

        await self.session.flush()
        return await self.get_by_id(staff_id, master_id)

    async def get_staff_for_user(
        self, master_id: int, user_id: int
    ) -> Optional[StaffMember]:
        """Retrieve staff profile associated with a specific Telegram user in this project."""
        query = select(StaffMember).where(
            StaffMember.master_id == master_id,
            StaffMember.user_id == user_id,
        )
        res = await self.session.execute(query)
        return res.scalars().first()
