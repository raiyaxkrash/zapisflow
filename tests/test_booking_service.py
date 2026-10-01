"""Tests for BookingService: reservation, validation, and PostgreSQL exclusion handling."""

from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.exc import IntegrityError

from app.database.models.appointment import AppointmentStatus
from app.database.models.service import DepositType, Service
from app.database.models.user import User
from app.services.booking_service import BookingService
from app.services.exceptions import (
    ServiceNotFoundError,
    SlotAlreadyBookedError,
    UserNotFoundError,
)


@pytest.mark.asyncio
async def test_booking_service_validates_user_and_service() -> None:
    session = AsyncMock()
    service = BookingService(session)

    # 1. Non-existent user
    service.user_repo = AsyncMock()
    service.user_repo.get_by_id.return_value = None

    start = datetime(2026, 10, 15, 12, 0, tzinfo=timezone.utc)
    with pytest.raises(UserNotFoundError):
        await service.create_hold_booking(user_id=999, service_id=1, start_time=start, master_id=1)

    # 2. Non-existent service
    service.user_repo.get_by_id.return_value = User(id=1, telegram_id=123, first_name="Тест")
    service.service_repo = AsyncMock()
    service.service_repo.get_by_id.return_value = None

    with pytest.raises(ServiceNotFoundError):
        await service.create_hold_booking(user_id=1, service_id=999, start_time=start, master_id=1)


@pytest.mark.asyncio
async def test_booking_service_catches_postgres_exclusion_violation() -> None:
    """When PostgreSQL raises an exclusion violation (code 23P01), BookingService maps it to SlotAlreadyBookedError."""
    session = AsyncMock()
    session.add = MagicMock()
    service = BookingService(session)

    service.user_repo = AsyncMock()
    service.user_repo.get_by_id.return_value = User(id=1, telegram_id=123456, first_name="Анна")

    svc = Service(
        id=10,
        master_id=1,
        title="Стрижка",
        price=Decimal("2000.00"),
        duration_min=60,
        buffer_min=15,
        deposit_type=DepositType.FIXED,
        deposit_value=Decimal("500.00"),
        is_active=True,
        is_archived=False,
    )
    service.service_repo = AsyncMock()
    service.service_repo.get_by_id.return_value = svc

    service.master_settings_repo = AsyncMock()
    service.master_settings_repo.get_value.return_value = 30

    start = datetime(2026, 10, 15, 12, 0, tzinfo=timezone.utc)

    # Mock staff repository
    from app.database.models.staff import StaffMember
    service.staff_repo = AsyncMock()
    service.staff_repo.get_primary_or_default.return_value = StaffMember(id=1, master_id=1, display_name="Анна Мастер", is_active=True)
    service.staff_repo.list_services_for_staff.return_value = [10]

    # Mock appointment repository
    service.appointment_repo = AsyncMock()
    service.appointment_repo.get_active_overlapping.return_value = []

    # Mock flush to raise PostgreSQL exclusion constraint error
    class MockOrigPgError:
        pgcode = "23P01"
        sqlstate = "23P01"
        message = "conflicting key value violates exclusion constraint 'no_overlapping_active_appointments'"

        def __str__(self):
            return self.message

    pg_exc = IntegrityError(
        statement="INSERT INTO appointments ...",
        params={},
        orig=MockOrigPgError(),
    )
    session.flush.side_effect = pg_exc

    with patch("app.services.booking_service.SlotEngine") as MockSlotEngine:
        mock_engine_instance = AsyncMock()
        mock_engine_instance.is_slot_available.return_value = True
        MockSlotEngine.return_value = mock_engine_instance

        with pytest.raises(SlotAlreadyBookedError) as exc_info:
            await service.create_hold_booking(
                user_id=1,
                service_id=10,
                start_time=start,
                master_id=1,
            )

    assert "Выбранный интервал только что был забронирован другим клиентом" in str(exc_info.value)
    assert session.rollback.await_count == 1
