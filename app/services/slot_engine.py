"""Slot calculation engine for master schedule, active bookings, buffers and breaks.

Strictly isolated per master_id.
"""

from datetime import date as dt_date, datetime, timedelta
from typing import List, Optional, Tuple
import pytz
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.settings import settings
from app.repositories.appointment_repository import AppointmentRepository
from app.repositories.master_settings_repository import MasterSettingsRepository
from app.repositories.schedule_repository import ScheduleRepository
from app.repositories.service_repository import ServiceRepository
from app.services.exceptions import ServiceNotFoundError


class SlotEngine:
    """Core engine responsible for dynamic slot generation and collision detection strictly within master_id."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.schedule_repo = ScheduleRepository(session)
        self.appointment_repo = AppointmentRepository(session)
        self.service_repo = ServiceRepository(session)
        self.master_settings_repo = MasterSettingsRepository(session)

    async def get_available_slots(
        self,
        service_id: int,
        target_date: dt_date,
        master_id: int,
        *,
        duration_min: Optional[int] = None,
        buffer_min: Optional[int] = None,
        exclude_appointment_id: Optional[int] = None,
        allow_inactive: bool = False,
    ) -> List[datetime]:
        """Calculate and return all valid slot start datetimes for a given service and date within master_id."""
        # 1. Fetch and validate service strictly for this master
        service = await self.service_repo.get_by_id(service_id, master_id=master_id)
        if not service or (not allow_inactive and (not service.is_active or service.is_archived)):
            raise ServiceNotFoundError(f"Service id {service_id} not found or inactive for master {master_id}")

        # 2. Timezone configuration from master_settings
        tz_str = await self.master_settings_repo.get_value(master_id, "timezone", settings.timezone)
        tz = pytz.timezone(tz_str)
        now_local = datetime.now(tz)
        today_local = now_local.date()

        # 3. Date boundaries validation from master_settings
        max_advance_days = int(
            await self.master_settings_repo.get_value(
                master_id, "booking_horizon_days", settings.max_advance_days
            )
        )
        min_advance_hours = int(
            await self.master_settings_repo.get_value(
                master_id, "min_advance_hours", settings.min_advance_hours
            )
        )
        grid_step_minutes = int(
            await self.master_settings_repo.get_value(
                master_id, "grid_step_minutes", settings.grid_step_minutes
            )
        )

        if target_date < today_local:
            return []
        if target_date > today_local + timedelta(days=max_advance_days):
            return []

        min_candidate_time = now_local + timedelta(hours=min_advance_hours) if target_date == today_local else None

        # 4. Determine working hours and breaks for the target date for this master
        work_hours = await self._get_working_window_and_breaks(master_id, target_date, tz)
        if not work_hours:
            return []  # Day off or closed

        work_start_dt, work_end_dt, breaks = work_hours

        # 5. Collect all busy intervals for the day strictly for this master
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
        master_id: int,
        *,
        duration_min: Optional[int] = None,
        buffer_min: Optional[int] = None,
        exclude_appointment_id: Optional[int] = None,
        allow_inactive: bool = False,
    ) -> bool:
        """Check if a single specific datetime slot is still free for this master."""
        if slot_start.tzinfo is None:
            return False
        tz_str = await self.master_settings_repo.get_value(master_id, "timezone", settings.timezone)
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
        master_id: int,
        days_count: int = 14,
    ) -> List[dt_date]:
        """Scan a date range and return only dates with at least one free slot for this master."""
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
        """Resolve working window and breaks considering date exceptions and weekly templates for this master."""
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
            b_start_dt = tz.localize(datetime.combine(target_date, b_start))
            b_end_dt = tz.localize(datetime.combine(target_date, b_end))
            breaks_dt.append((b_start_dt, b_end_dt))

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
        """Gather all blocked intervals strictly for this master_id."""
        busy: List[Tuple[datetime, datetime]] = list(breaks)

        # 1. Active appointments for this master
        appointments = await self.appointment_repo.get_active_for_range(
            master_id=master_id,
            start_datetime=day_start,
            end_datetime=day_end,
            exclude_id=exclude_appointment_id,
        )
        for app in appointments:
            busy.append((app.start_time, app.end_time_with_buffer))

        # 2. Blocked intervals for this master
        blocked = await self.schedule_repo.get_blocked_intervals(
            master_id=master_id,
            start_datetime=day_start,
            end_datetime=day_end,
        )
        for blk in blocked:
            busy.append((blk.start_time, blk.end_time))

        return busy

    @staticmethod
    def _merge_intervals(
        intervals: List[Tuple[datetime, datetime]]
    ) -> List[Tuple[datetime, datetime]]:
        """Merge overlapping or contiguous datetime intervals."""
        if not intervals:
            return []

        sorted_intervals = sorted(intervals, key=lambda x: x[0])
        merged: List[Tuple[datetime, datetime]] = []

        curr_start, curr_end = sorted_intervals[0]
        for next_start, next_end in sorted_intervals[1:]:
            if next_start <= curr_end:
                curr_end = max(curr_end, next_end)
            else:
                merged.append((curr_start, curr_end))
                curr_start, curr_end = next_start, next_end

        merged.append((curr_start, curr_end))
        return merged
