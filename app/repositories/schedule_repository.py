"""
Schedule repository for templates, exceptions, breaks and blocked intervals.
"""

from datetime import date as dt_date, datetime, time as dt_time
from typing import List, Optional, Sequence, Tuple
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database.models.schedule import (
    BlockedInterval,
    ScheduleException,
    ScheduleExceptionBreak,
    ScheduleTemplate,
    ScheduleTemplateBreak,
)


class ScheduleRepository:
    """
    Repository for managing master schedules, calendar overrides and blocked slots.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_weekly_templates(self, master_id: int = 1) -> Sequence[ScheduleTemplate]:
        """
        Get all 7 days schedule templates for the master.
        """
        query = (
            select(ScheduleTemplate)
            .where(ScheduleTemplate.master_id == master_id)
            .options(selectinload(ScheduleTemplate.breaks))
            .order_by(ScheduleTemplate.day_of_week.asc())
        )
        result = await self.session.execute(query)
        return result.scalars().all()

    async def get_template_for_weekday(
        self, weekday: int, master_id: int = 1
    ) -> Optional[ScheduleTemplate]:
        """
        Get template for a specific weekday (0=Mon, ..., 6=Sun).
        """
        query = (
            select(ScheduleTemplate)
            .where(
                ScheduleTemplate.master_id == master_id,
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
        master_id: int = 1,
    ) -> ScheduleTemplate:
        """
        Create or update a weekday template and its breaks.
        """
        template = await self.get_template_for_weekday(weekday, master_id=master_id)
        if not template:
            template = ScheduleTemplate(
                master_id=master_id,
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
        self, target_date: dt_date, master_id: int = 1
    ) -> Optional[ScheduleException]:
        """
        Get calendar exception override for a specific date if exists.
        """
        query = (
            select(ScheduleException)
            .where(
                ScheduleException.master_id == master_id,
                ScheduleException.date == target_date,
            )
            .options(selectinload(ScheduleException.breaks))
        )
        result = await self.session.execute(query)
        return result.scalars().first()

    async def set_date_exception(
        self,
        target_date: dt_date,
        is_day_off: bool,
        work_start: Optional[dt_time] = None,
        work_end: Optional[dt_time] = None,
        comment: Optional[str] = None,
        breaks: Optional[List[Tuple[dt_time, dt_time]]] = None,
        master_id: int = 1,
    ) -> ScheduleException:
        """
        Create or override an exception for a specific date.
        """
        exception = await self.get_exception_for_date(target_date, master_id=master_id)
        if not exception:
            exception = ScheduleException(
                master_id=master_id,
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

    async def delete_date_exception(self, target_date: dt_date, master_id: int = 1) -> bool:
        """
        Remove date exception, reverting the date to default weekly template.
        """
        stmt = delete(ScheduleException).where(
            ScheduleException.master_id == master_id,
            ScheduleException.date == target_date,
        )
        result = await self.session.execute(stmt)
        await self.session.flush()
        return result.rowcount > 0

    async def get_blocked_intervals(
        self, start_datetime: datetime, end_datetime: datetime, master_id: int = 1
    ) -> Sequence[BlockedInterval]:
        """
        Get all blocked intervals overlapping with the given range.
        """
        query = select(BlockedInterval).where(
            BlockedInterval.master_id == master_id,
            BlockedInterval.start_time < end_datetime,
            BlockedInterval.end_time > start_datetime,
        ).order_by(BlockedInterval.start_time.asc())
        result = await self.session.execute(query)
        return result.scalars().all()

    async def create_blocked_interval(
        self,
        start_time: datetime,
        end_time: datetime,
        reason: Optional[str] = None,
        created_by_admin_id: Optional[int] = None,
        master_id: int = 1,
    ) -> BlockedInterval:
        """
        Block a time slot manually.
        """
        interval = BlockedInterval(
            master_id=master_id,
            start_time=start_time,
            end_time=end_time,
            reason=reason,
            created_by_admin_id=created_by_admin_id,
        )
        self.session.add(interval)
        await self.session.flush()
        await self.session.refresh(interval)
        return interval

    async def delete_blocked_interval(self, interval_id: int) -> bool:
        """
        Remove a manual time block.
        """
        stmt = delete(BlockedInterval).where(BlockedInterval.id == interval_id)
        result = await self.session.execute(stmt)
        await self.session.flush()
        return result.rowcount > 0
