"""
Schedule repository for templates, exceptions, breaks and blocked intervals strictly scoped to master_id and staff_id.
Supports both project-wide studio closures (staff_id IS NULL) and staff-specific personal schedules.
"""

from datetime import date as dt_date, datetime, time as dt_time
from typing import List, Optional, Sequence, Tuple
from sqlalchemy import delete, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database.models.schedule import (
    BlockedInterval,
    ScheduleException,
    ScheduleExceptionBreak,
    ScheduleTemplate,
    ScheduleTemplateBreak,
)
from app.database.models.staff import StaffMember


class ScheduleRepository:
    """Repository for managing master and staff schedules, calendar overrides and blocked slots."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def _resolve_staff_id(self, master_id: int, staff_id: Optional[int] = None) -> Optional[int]:
        """Resolve specific staff_id or fall back to primary/default staff member for master_id."""
        if staff_id is not None:
            return staff_id
        q = (
            select(StaffMember.id)
            .where(StaffMember.master_id == master_id, StaffMember.is_active.is_(True))
            .order_by(StaffMember.sort_order.asc(), StaffMember.id.asc())
            .limit(1)
        )
        res = await self.session.execute(q)
        sid = res.scalars().first()
        if not sid:
            fallback_q = (
                select(StaffMember.id)
                .where(StaffMember.master_id == master_id)
                .order_by(StaffMember.id.asc())
                .limit(1)
            )
            sid = (await self.session.execute(fallback_q)).scalars().first()
        if not sid:
            from app.database.models.master import Master
            m_res = await self.session.execute(select(Master).where(Master.id == master_id))
            master_obj = m_res.scalars().first()
            if master_obj:
                new_staff = StaffMember(
                    master_id=master_id,
                    display_name=master_obj.display_name or "Основной мастер",
                    is_active=True,
                    sort_order=0,
                )
                self.session.add(new_staff)
                await self.session.flush()
                sid = new_staff.id
        return sid

    async def get_weekly_templates(
        self, master_id: int, staff_id: Optional[int] = None
    ) -> Sequence[ScheduleTemplate]:
        """Get all 7 days schedule templates for the staff member."""
        resolved_staff_id = await self._resolve_staff_id(master_id, staff_id)
        if not resolved_staff_id:
            return []
        query = (
            select(ScheduleTemplate)
            .where(
                ScheduleTemplate.master_id == master_id,
                ScheduleTemplate.staff_id == resolved_staff_id,
            )
            .options(selectinload(ScheduleTemplate.breaks))
            .order_by(ScheduleTemplate.day_of_week.asc())
        )
        result = await self.session.execute(query)
        return result.scalars().all()

    async def get_template_for_weekday(
        self, weekday: int, master_id: int, staff_id: Optional[int] = None
    ) -> Optional[ScheduleTemplate]:
        """Get template for a specific weekday (0=Mon, ..., 6=Sun) of the given staff member."""
        resolved_staff_id = await self._resolve_staff_id(master_id, staff_id)
        if not resolved_staff_id:
            return None
        query = (
            select(ScheduleTemplate)
            .where(
                ScheduleTemplate.master_id == master_id,
                ScheduleTemplate.staff_id == resolved_staff_id,
                ScheduleTemplate.day_of_week == weekday,
            )
            .options(selectinload(ScheduleTemplate.breaks))
        )
        result = await self.session.execute(query)
        return result.scalars().first()

    async def set_template(
        self,
        weekday: int,
        is_day_off: bool,
        work_start: dt_time = dt_time(10, 0),
        work_end: dt_time = dt_time(19, 0),
        breaks: Optional[List[Tuple[dt_time, dt_time]]] = None,
        *,
        master_id: int,
        staff_id: Optional[int] = None,
    ) -> ScheduleTemplate:
        """Create or update a weekday template and its breaks for a specific staff member."""
        resolved_staff_id = await self._resolve_staff_id(master_id, staff_id)
        if not resolved_staff_id:
            raise ValueError(f"No staff member found for master {master_id}")

        template = await self.get_template_for_weekday(weekday, master_id=master_id, staff_id=resolved_staff_id)
        if not template:
            template = ScheduleTemplate(
                master_id=master_id,
                staff_id=resolved_staff_id,
                day_of_week=weekday,
                is_day_off=is_day_off,
                work_start=work_start,
                work_end=work_end,
            )
            self.session.add(template)
            await self.session.flush()
        else:
            template.is_day_off = is_day_off
            template.work_start = work_start
            template.work_end = work_end

            # Clear existing breaks
            await self.session.execute(
                delete(ScheduleTemplateBreak).where(ScheduleTemplateBreak.template_id == template.id)
            )

        if breaks and not is_day_off:
            for b_start, b_end in breaks:
                b = ScheduleTemplateBreak(
                    template_id=template.id, break_start=b_start, break_end=b_end
                )
                self.session.add(b)

        await self.session.flush()
        await self.session.refresh(template)
        return template

    async def get_exception_for_date(
        self, target_date: dt_date, master_id: int, staff_id: Optional[int] = None
    ) -> Optional[ScheduleException]:
        """
        Get calendar exception override for a specific date.
        Strict precedence:
        1. Project-wide exception (staff_id IS NULL) applies to all staff.
        2. Staff-specific exception (staff_id == resolved_staff_id).
        """
        # 1. Check project-wide studio exception first
        project_query = (
            select(ScheduleException)
            .where(
                ScheduleException.master_id == master_id,
                ScheduleException.staff_id.is_(None),
                ScheduleException.date == target_date,
            )
            .options(selectinload(ScheduleException.breaks))
        )
        project_exc = (await self.session.execute(project_query)).scalars().first()
        if project_exc:
            return project_exc

        # 2. Check staff-specific exception
        resolved_staff_id = await self._resolve_staff_id(master_id, staff_id)
        if not resolved_staff_id:
            return None

        staff_query = (
            select(ScheduleException)
            .where(
                ScheduleException.master_id == master_id,
                ScheduleException.staff_id == resolved_staff_id,
                ScheduleException.date == target_date,
            )
            .options(selectinload(ScheduleException.breaks))
        )
        return (await self.session.execute(staff_query)).scalars().first()

    async def set_date_exception(
        self,
        target_date: dt_date,
        is_day_off: bool,
        work_start: Optional[dt_time] = None,
        work_end: Optional[dt_time] = None,
        comment: Optional[str] = None,
        breaks: Optional[List[Tuple[dt_time, dt_time]]] = None,
        *,
        master_id: int,
        staff_id: Optional[int] = None,
    ) -> ScheduleException:
        """
        Create or override an exception for a specific date and master.
        staff_id is None -> Project-wide closure.
        staff_id is given -> Personal staff override.
        """
        query = select(ScheduleException).where(
            ScheduleException.master_id == master_id,
            ScheduleException.date == target_date,
        )
        if staff_id is None:
            query = query.where(ScheduleException.staff_id.is_(None))
        else:
            query = query.where(ScheduleException.staff_id == staff_id)

        exception = (await self.session.execute(query)).scalars().first()
        if not exception:
            exception = ScheduleException(
                master_id=master_id,
                staff_id=staff_id,
                date=target_date,
                is_day_off=is_day_off,
                work_start=work_start,
                work_end=work_end,
                comment=comment,
            )
            self.session.add(exception)
            await self.session.flush()
        else:
            exception.is_day_off = is_day_off
            exception.work_start = work_start
            exception.work_end = work_end
            exception.comment = comment

            # Clear existing breaks
            await self.session.execute(
                delete(ScheduleExceptionBreak).where(
                    ScheduleExceptionBreak.exception_id == exception.id
                )
            )

        if breaks and not is_day_off:
            for b_start, b_end in breaks:
                b = ScheduleExceptionBreak(
                    exception_id=exception.id, break_start=b_start, break_end=b_end
                )
                self.session.add(b)

        await self.session.flush()
        await self.session.refresh(exception)
        return exception

    async def delete_date_exception(
        self, target_date: dt_date, master_id: int, staff_id: Optional[int] = None
    ) -> bool:
        """Remove date exception (project-wide if staff_id is None, staff-specific otherwise)."""
        stmt = delete(ScheduleException).where(
            ScheduleException.master_id == master_id,
            ScheduleException.date == target_date,
        )
        if staff_id is None:
            stmt = stmt.where(ScheduleException.staff_id.is_(None))
        else:
            stmt = stmt.where(ScheduleException.staff_id == staff_id)

        result = await self.session.execute(stmt)
        await self.session.flush()
        return result.rowcount > 0

    async def get_blocked_intervals(
        self,
        start_datetime: datetime,
        end_datetime: datetime,
        master_id: int,
        staff_id: Optional[int] = None,
    ) -> Sequence[BlockedInterval]:
        """
        Get blocked intervals overlapping with range.
        Returns both project-wide blocked intervals (staff_id IS NULL) and staff-specific blocks.
        """
        resolved_staff_id = await self._resolve_staff_id(master_id, staff_id) if staff_id is not None else None
        staff_condition = BlockedInterval.staff_id.is_(None)
        if resolved_staff_id is not None:
            staff_condition = or_(BlockedInterval.staff_id.is_(None), BlockedInterval.staff_id == resolved_staff_id)

        query = (
            select(BlockedInterval)
            .where(
                BlockedInterval.master_id == master_id,
                BlockedInterval.start_time < end_datetime,
                BlockedInterval.end_time > start_datetime,
                staff_condition,
            )
            .order_by(BlockedInterval.start_time.asc())
        )
        result = await self.session.execute(query)
        return result.scalars().all()

    async def create_blocked_interval(
        self,
        start_time: datetime,
        end_time: datetime,
        master_id: int,
        staff_id: Optional[int] = None,
        reason: Optional[str] = None,
        created_by_admin_id: Optional[int] = None,
    ) -> BlockedInterval:
        """Block a time slot manually (project-wide if staff_id is None, or personal staff block)."""
        interval = BlockedInterval(
            master_id=master_id,
            staff_id=staff_id,
            start_time=start_time,
            end_time=end_time,
            reason=reason,
            created_by_admin_id=created_by_admin_id,
        )
        self.session.add(interval)
        await self.session.flush()
        await self.session.refresh(interval)
        return interval

    async def delete_blocked_interval(self, interval_id: int, master_id: int) -> bool:
        """Remove a manual time block ensuring it belongs to this master."""
        stmt = delete(BlockedInterval).where(
            BlockedInterval.id == interval_id,
            BlockedInterval.master_id == master_id,
        )
        result = await self.session.execute(stmt)
        await self.session.flush()
        return result.rowcount > 0
