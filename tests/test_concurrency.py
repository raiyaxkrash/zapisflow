"""Concurrency tests: race condition between two bookings for the same slot."""

import asyncio
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy.exc import IntegrityError

from app.database.models.service import DepositType, Service
from app.database.models.user import User
from app.services.booking_service import BookingService
from app.services.exceptions import SlotAlreadyBookedError


@pytest.mark.asyncio
async def test_concurrent_booking_race_condition() -> None:
    """Simulates two parallel booking attempts for the same interval.
    The first one succeeds, while the second one fails with SlotAlreadyBookedError
    due to PostgreSQL exclusion violation / concurrency check.
    """
    first_session = AsyncMock()
    second_session = AsyncMock()

    service_a = BookingService(first_session)
    service_b = BookingService(second_session)

    # Setup mock users
    service_a.user_repo = AsyncMock()
    service_a.user_repo.get_by_id.return_value = User(id=1, telegram_id=111, first_name="Client 1")

    service_b.user_repo = AsyncMock()
    service_b.user_repo.get_by_id.return_value = User(id=2, telegram_id=222, first_name="Client 2")

    # Setup mock service
    service_obj = Service(
        id=5,
        master_id=1,
        title="Окрашивание",
        price=Decimal("3000.00"),
        duration_min=90,
        buffer_min=15,
        deposit_type=DepositType.FIXED,
        deposit_value=Decimal("1000.00"),
        is_active=True,
        is_archived=False,
    )
    service_a.service_repo = AsyncMock()
    service_a.service_repo.get_by_id.return_value = service_obj
    service_b.service_repo = AsyncMock()
    service_b.service_repo.get_by_id.return_value = service_obj

    start_time = datetime(2026, 11, 20, 14, 0, tzinfo=timezone.utc)

    # Mock repos
    service_a.appointment_repo = AsyncMock()
    service_a.appointment_repo.get_active_overlapping.return_value = []
    service_a.master_settings_repo = AsyncMock()
    service_a.master_settings_repo.get_value.return_value = 30
    service_a.master_settings_repo.get_by_master_id.return_value = SimpleNamespace(
        bank_name="Test Bank", bank_card_number="4111111111111111", bank_recipient_name="Test Owner"
    )

    service_b.appointment_repo = AsyncMock()
    service_b.appointment_repo.get_active_overlapping.return_value = []
    service_b.master_settings_repo = AsyncMock()
    service_b.master_settings_repo.get_value.return_value = 30
    service_b.master_settings_repo.get_by_master_id.return_value = SimpleNamespace(
        bank_name="Test Bank", bank_card_number="4111111111111111", bank_recipient_name="Test Owner"
    )

    from app.database.models.staff import StaffMember
    from unittest.mock import MagicMock
    staff = StaffMember(id=1, master_id=1, display_name="Мастер", is_active=True)
    service_a.staff_repo = AsyncMock()
    service_a.staff_repo.get_primary_or_default.return_value = staff
    service_a.staff_repo.list_services_for_staff.return_value = [5]

    service_b.staff_repo = AsyncMock()
    service_b.staff_repo.get_primary_or_default.return_value = staff
    service_b.staff_repo.list_services_for_staff.return_value = [5]

    first_session.add = MagicMock()
    second_session.add = MagicMock()

    # First session flush succeeds
    first_session.flush = AsyncMock()
    # Second session flush fails with PostgreSQL exclusion violation
    class MockPgExclusion:
        pgcode = "23P01"
        sqlstate = "23P01"
        def __str__(self):
            return "exclusion constraint violation: no_overlapping_active_appointments"

    second_session.flush.side_effect = IntegrityError(
        statement="INSERT INTO appointments",
        params={},
        orig=MockPgExclusion(),
    )

    with patch("app.services.booking_service.SlotEngine") as MockSlotEngine:
        mock_engine = AsyncMock()
        mock_engine.is_slot_available.return_value = True
        MockSlotEngine.return_value = mock_engine

        # Task 1: user 1 books slot
        task1 = service_a.create_hold_booking(
            user_id=1,
            service_id=5,
            start_time=start_time,
            master_id=1,
        )

        # Task 2: user 2 tries to book the same slot at the same time
        task2 = service_b.create_hold_booking(
            user_id=2,
            service_id=5,
            start_time=start_time,
            master_id=1,
        )

        # Run both tasks concurrently
        results = await asyncio.gather(task1, task2, return_exceptions=True)

    # One succeeded, one was rejected with SlotAlreadyBookedError
    successes = [r for r in results if not isinstance(r, Exception)]
    errors = [r for r in results if isinstance(r, SlotAlreadyBookedError)]

    assert len(successes) == 1
    assert len(errors) == 1
    assert "Выбранный интервал только что был забронирован другим клиентом" in str(errors[0])
