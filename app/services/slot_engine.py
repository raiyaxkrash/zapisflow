"""
Slot calculation engine for master schedule, active bookings, buffers and breaks.
"""

from datetime import date as dt_date, datetime, timedelta
from typing import List, Optional, Tuple
import pytz
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.settings import settings
from app.repositories.appointment_repository import AppointmentRepository
from app.repositories.schedule_repository import ScheduleRepository
from app.repositories.service_repository import ServiceRepository
from app.repositories.settings_repository import SettingsRepository
from app.services.exceptions import ServiceNotFoundError


class SlotEngine:
    """
    Core engine responsible for dynamic slot generation and collision detection.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.schedule_repo = ScheduleRepository(session)
        self.appointment_repo = AppointmentRepository(session)
        self.service_repo = ServiceRepository(session)
        self.settings_repo = SettingsRepository(session)

    async def get_available_slots(
        self,
        service_id: int,
        target_date: dt_date,
        master_id: int = 1,
        *,
        duration_min: Optional[int] = None,
        buffer_min: Optional[int] = None,
        exclude_appointment_id: Optional[int] = None,
        allow_inactive: bool = False,
    ) -> List[datetime]:
        """
        Calculate and return all valid slot start datetimes for a given service and date.
        """
        # 1. Fetch and validate service
        service = await self.service_repo.get_by_id(service_id)
        if not service or (not allow_inactive and (not service.is_active or service.is_archived)):
            raise ServiceNotFoundError(f"Service id {service_id} not found or inactive")

        # 2. Timezone configuration
        tz_str = await self.settings_repo.get_value("timezone", settings.timezone)
        tz = pytz.timezone(tz_str)
        now_local = datetime.now(tz)
        today_local = now_local.date()

        # 3. Date boundaries validation
        max_advance_days = int(
            await self.settings_repo.get_value("max_advance_days", settings.max_advance_days)
        )
        min_advance_hours = int(
            await self.settings_repo.get_value("min_advance_hours", settings.min_advance_hours)
        )
        grid_step_minutes = int(
            await self.settings_repo.get_value("grid_step_minutes", settings.grid_step_minutes)
        )

        if target_date < today_local:
            return []
        if target_date > today_local + timedelta(days=max_advance_days):
            return []

        min_candidate_time = now_local + timedelta(hours=min_advance_hours) if target_date == today_local else None

        # 4. Determine working hours and breaks for the target date
        work_hours = await self._get_working_window_and_breaks(master_id, target_date, tz)
        if not work_hours:
            return []  # Day off or closed

        work_start_dt, work_end_dt, breaks = work_hours

        # 5. Collect all busy intervals for the day
        busy_intervals = await self._collect_busy_intervals(
            master_id=master_id,
            target_date=target_date,
            day_start=work_start_dt,
            day_end=work_end_dt,
            breaks=breaks,
            tz=tz,
            exclude_appointment_id=exclude_appointment_id,
        )

        # 6. Merge busy intervals
        merged_busy = self._merge_intervals(busy_intervals)

        # 7. Generate candidate slots
        duration = timedelta(minutes=duration_min if duration_min is not None else service.duration_min)
        buffer = timedelta(minutes=buffer_min if buffer_min is not None else service.buffer_min)
        total_block = duration + buffer
        step = timedelta(minutes=grid_step_minutes)

        available_slots: List[datetime] = []
        current_slot = work_start_dt

        while current_slot + total_block <= work_end_dt:
            # Check minimum advance booking time for today
            if min_candidate_time and current_slot < min_candidate_time:
                current_slot += step
                continue

            slot_end_with_buffer = current_slot + total_block

            # Verify collision with any merged busy interval
            has_collision = False
            for b_start, b_end in merged_busy:
                # Interval overlap condition: cand_start < b_end and cand_end > b_start
                if current_slot < b_end and slot_end_with_buffer > b_start:
                    has_collision = True
                    break

            if not has_collision:
                available_slots.append(current_slot)

            current_slot += step

        return available_slots

    async def is_slot_available(
        self,
        service_id: int,
        slot_start: datetime,
        master_id: int = 1,
        *,
        duration_min: Optional[int] = None,
        buffer_min: Optional[int] = None,
        exclude_appointment_id: Optional[int] = None,
        allow_inactive: bool = False,
    ) -> bool:
        """
        Check if a single specific datetime slot is still free.
        """
        if slot_start.tzinfo is None:
            return False
        tz_str = await self.settings_repo.get_value("timezone", settings.timezone)
        target_date = slot_start.astimezone(pytz.timezone(tz_str)).date()
        available = await self.get_available_slots(
            service_id,
            target_date,
            master_id=master_id,
            duration_min=duration_min,
            buffer_min=buffer_min,
            exclude_appointment_id=exclude_appointment_id,
            allow_inactive=allow_inactive,
        )
        # Compare timestamps ignoring microsecond differences
        slot_ts = int(slot_start.timestamp())
        return any(int(s.timestamp()) == slot_ts for s in available)

    async def get_available_dates(
        self,
        service_id: int,
        start_date: dt_date,
        days_count: int = 14,
        master_id: int = 1,
    ) -> List[dt_date]:
        """
        Scan a date range and return only dates that have at least one free slot.
        Used by the interactive inline calendar to highlight available days.
        """
        available_dates: List[dt_date] = []
        for offset in range(days_count):
            curr_date = start_date + timedelta(days=offset)
            slots = await self.get_available_slots(service_id, curr_date, master_id=master_id)
            if slots:
                available_dates.append(curr_date)
        return available_dates

    async def _get_working_window_and_breaks(
        self, master_id: int, target_date: dt_date, tz: pytz.BaseTzInfo
    ) -> Optional[Tuple[datetime, datetime, List[Tuple[datetime, datetime]]]]:
        """
        Resolve working window and breaks considering date exceptions and weekly templates.
        """
        exception = await self.schedule_repo.get_exception_for_date(target_date, master_id=master_id)
        if exception:
            if exception.is_day_off or not exception.work_start or not exception.work_end:
                return None
            work_start = exception.work_start
            work_end = exception.work_end
            raw_breaks = [(b.break_start, b.break_end) for b in exception.breaks]
        else:
            weekday = target_date.weekday()
            template = await self.schedule_repo.get_template_for_weekday(weekday, master_id=master_id)
            if not template or template.is_day_off:
                return None
            work_start = template.work_start
            work_end = template.work_end
            raw_breaks = [(b.break_start, b.break_end) for b in template.breaks]

        # Convert TIME to localized datetime
        work_start_dt = tz.localize(datetime.combine(target_date, work_start))
        work_end_dt = tz.localize(datetime.combine(target_date, work_end))

        breaks_dt: List[Tuple[datetime, datetime]] = []
        for b_start, b_end in raw_breaks:
            b_s = tz.localize(datetime.combine(target_date, b_start))
            b_e = tz.localize(datetime.combine(target_date, b_end))
            breaks_dt.append((b_s, b_e))

        return work_start_dt, work_end_dt, breaks_dt

    async def _collect_busy_intervals(
        self,
        master_id: int,
        target_date: dt_date,
        day_start: datetime,
        day_end: datetime,
        breaks: List[Tuple[datetime, datetime]],
        tz: pytz.BaseTzInfo,
        exclude_appointment_id: Optional[int] = None,
    ) -> List[Tuple[datetime, datetime]]:
        """
        Aggregate breaks, manual blocked intervals and active appointments.
        """
        busy_intervals: List[Tuple[datetime, datetime]] = list(breaks)

        # 1. Manual blocked intervals
        blocked = await self.schedule_repo.get_blocked_intervals(
            start_datetime=day_start,
            end_datetime=day_end + timedelta(hours=4),  # check into night
            master_id=master_id,
        )
        for b in blocked:
            blocked_start = b.start_time
            blocked_end = b.end_time
            if blocked_start.tzinfo is None:
                blocked_start = blocked_start.replace(tzinfo=pytz.UTC)
            if blocked_end.tzinfo is None:
                blocked_end = blocked_end.replace(tzinfo=pytz.UTC)
            busy_intervals.append((blocked_start.astimezone(tz), blocked_end.astimezone(tz)))

        # 2. Active appointments for the day
        appointments = await self.appointment_repo.get_active_for_range(
            master_id=master_id,
            start_datetime=day_start,
            end_datetime=day_end + timedelta(hours=4),
            exclude_id=exclude_appointment_id,
        )
        for app in appointments:
            appointment_start = app.start_time
            appointment_end = app.end_time_with_buffer
            if appointment_start.tzinfo is None:
                appointment_start = appointment_start.replace(tzinfo=pytz.UTC)
            if appointment_end.tzinfo is None:
                appointment_end = appointment_end.replace(tzinfo=pytz.UTC)
            busy_intervals.append(
                (appointment_start.astimezone(tz), appointment_end.astimezone(tz))
            )

        return busy_intervals

    @staticmethod
    def _merge_intervals(
        intervals: List[Tuple[datetime, datetime]]
    ) -> List[Tuple[datetime, datetime]]:
        """
        Sort and merge overlapping or contiguous datetime intervals.
        """
        if not intervals:
            return []

        sorted_intervals = sorted(intervals, key=lambda x: x[0])
        merged: List[Tuple[datetime, datetime]] = [sorted_intervals[0]]

        for current_start, current_end in sorted_intervals[1:]:
            last_start, last_end = merged[-1]
            if current_start <= last_end:
                # Overlap or contiguous: extend previous interval
                merged[-1] = (last_start, max(last_end, current_end))
            else:
                merged.append((current_start, current_end))

        return merged
