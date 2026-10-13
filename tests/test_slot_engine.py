"""
Unit tests for SlotEngine interval merging and collision calculations.
"""

from datetime import date as dt_date, datetime, time as dt_time, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock
import pytest
import pytz

from app.database.models.appointment import Appointment, AppointmentStatus
from app.database.models.schedule import ScheduleTemplate
from app.database.models.service import DepositType, Service
from app.services.slot_engine import SlotEngine


def test_merge_intervals_empty():
    """
    Test that merging an empty list of intervals returns an empty list.
    """
    assert SlotEngine._merge_intervals([]) == []


def test_merge_intervals_overlapping():
    """
    Test merging overlapping and contiguous datetime intervals.
    """
    tz = pytz.timezone("Europe/Moscow")
    base = tz.localize(datetime(2026, 10, 15, 10, 0))

    # [10:00, 11:00], [10:30, 12:00] -> should merge to [10:00, 12:00]
    int1 = (base, base + timedelta(hours=1))
    int2 = (base + timedelta(minutes=30), base + timedelta(hours=2))

    merged = SlotEngine._merge_intervals([int1, int2])
    assert len(merged) == 1
    assert merged[0] == (base, base + timedelta(hours=2))


def test_merge_intervals_disjoint():
    """
    Test disjoint intervals remain separate.
    """
    tz = pytz.timezone("Europe/Moscow")
    base = tz.localize(datetime(2026, 10, 15, 10, 0))

    int1 = (base, base + timedelta(hours=1))
    int2 = (base + timedelta(hours=2), base + timedelta(hours=3))

    merged = SlotEngine._merge_intervals([int1, int2])
    assert len(merged) == 2
    assert merged[0] == int1
    assert merged[1] == int2


@pytest.mark.asyncio
async def test_slot_engine_calculation_with_active_booking():
    """
    Test slot calculation when a working day has an active appointment with buffer.
    """
    tz = pytz.timezone("Europe/Moscow")
    mock_session = AsyncMock()
    engine = SlotEngine(mock_session)

    # Mock service: 90 min duration, 30 min buffer (total block = 120 min = 2h)
    mock_service = Service(
        id=1,
        master_id=1,
        title="Маникюр с покрытием",
        price=Decimal("2500.00"),
        duration_min=90,
        buffer_min=30,
        deposit_type=DepositType.FIXED,
        deposit_value=Decimal("500.00"),
        is_active=True,
        is_archived=False,
    )
    engine.service_repo.get_by_id = AsyncMock(return_value=mock_service)

    # Target date: future weekday
    target_date = dt_date(2026, 10, 20)

    # Mock working window: 10:00 - 16:00
    work_start_dt = tz.localize(datetime(2026, 10, 20, 10, 0))
    work_end_dt = tz.localize(datetime(2026, 10, 20, 16, 0))
    engine._get_working_window_and_breaks = AsyncMock(
        return_value=(work_start_dt, work_end_dt, [])
    )

    # Mock settings
    engine.master_settings_repo.get_value = AsyncMock(side_effect=lambda master_id, key, default: default)

    # Mock staff repo
    from app.database.models.staff import StaffMember
    engine.staff_repo = AsyncMock()
    engine.staff_repo.list_staff_for_service.return_value = []
    engine.staff_repo.get_primary_or_default.return_value = StaffMember(id=1, master_id=1, display_name="Мастер", is_active=True)

    # 1. First scenario: No appointments -> Slots from 10:00 to 14:00 (since 14:00 + 90m = 15:30 <= 16:00)
    engine._collect_busy_intervals = AsyncMock(return_value=[])

    slots = await engine.get_available_slots(service_id=1, target_date=target_date, master_id=1)
    # Expected slots with 30 min step:
    # 10:00, 10:30, 11:00, 11:30, 12:00, 12:30, 13:00, 13:30, 14:00 (14:30 + 90m = 16:00, with 30m buffer is 16:30 which ends after 16:00)
    assert len(slots) > 0
    slot_hours = [s.strftime("%H:%M") for s in slots]
    assert "10:00" in slot_hours
    assert "11:00" in slot_hours
    assert "14:00" in slot_hours

    # 2. Second scenario: Existing booking from 12:00 to 13:30, with buffer busy until 14:00
    # Any new 2-hour block (90m service + 30m buffer) overlapping with [12:00, 14:00) must be discarded
    booking_start = tz.localize(datetime(2026, 10, 20, 12, 0))
    booking_end_with_buffer = tz.localize(datetime(2026, 10, 20, 14, 0))
    engine._collect_busy_intervals = AsyncMock(return_value=[(booking_start, booking_end_with_buffer)])

    slots_with_booking = await engine.get_available_slots(service_id=1, target_date=target_date, master_id=1)
    slot_hours_with_booking = [s.strftime("%H:%M") for s in slots_with_booking]

    # Slot 10:00 (service 10:00-11:30, buffer to 12:00): ends at 12:00, exactly touches 12:00 without overlapping [12:00, 14:00) -> VALID!
    assert "10:00" in slot_hours_with_booking

    # Slot 10:30 (ends at 12:30 with buffer): overlaps [12:00, 14:00) -> MUST NOT BE IN SLOTS
    assert "10:30" not in slot_hours_with_booking
    assert "11:00" not in slot_hours_with_booking
    assert "11:30" not in slot_hours_with_booking
    assert "12:00" not in slot_hours_with_booking
    assert "12:30" not in slot_hours_with_booking
    assert "13:00" not in slot_hours_with_booking
    assert "13:30" not in slot_hours_with_booking

    # Slot 14:00 (service 14:00-15:30, buffer to 16:00): starts at 14:00 -> VALID!
    assert "14:00" in slot_hours_with_booking


@pytest.mark.asyncio
async def test_get_available_dates_respects_booking_horizon():
    """Verify get_available_dates caps scanning to master's booking_horizon_days."""
    mock_session = AsyncMock()
    engine = SlotEngine(mock_session)

    async def mock_get_value(master_id, key, default):
        if key == "booking_horizon_days":
            return 5
        return default

    engine.master_settings_repo.get_value = AsyncMock(side_effect=mock_get_value)
    engine.get_available_slots = AsyncMock(return_value=[datetime.now()])

    start_date = dt_date(2026, 10, 1)
    # Requesting 30 days should be constrained to 5
    dates = await engine.get_available_dates(
        service_id=1, start_date=start_date, master_id=1, days_count=30
    )
    assert len(dates) == 5
    assert dates[-1] == start_date + timedelta(days=4)

    # Default days_count=None should also use the 5-day horizon
    dates_default = await engine.get_available_dates(
        service_id=1, start_date=start_date, master_id=1
    )
    assert len(dates_default) == 5

