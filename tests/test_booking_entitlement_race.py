"""PostgreSQL serialization of booking creation and administrative suspension."""

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from app.database.models.appointment import Appointment
from app.database.models.master import Master, MasterSettings, SubscriptionStatus
from app.services.booking_service import BookingService
from app.services.exceptions import SubscriptionExpiredError
from app.services.slot_engine import SlotEngine
from app.services.subscription_service import SubscriptionService
from tests.conftest import requires_postgres
from tests.test_phase_9_subscription import _create_master, _create_service, _create_user


@requires_postgres
@pytest.mark.asyncio
async def test_suspension_waits_for_inflight_booking_transaction(pg_engine: AsyncEngine) -> None:
    sessions = async_sessionmaker(pg_engine, expire_on_commit=False)
    async with sessions() as session:
        owner = await _create_user(session, "Booking owner")
        client = await _create_user(session, "Booking client")
        master = await _create_master(
            session, owner.id, "Booking race master", SubscriptionStatus.ACTIVE,
            paid_until=datetime.now(timezone.utc) + timedelta(days=5),
        )
        service = await _create_service(session, master.id)
        payment_settings = await session.get(MasterSettings, master.id)
        payment_settings.bank_name = "Test Bank"
        payment_settings.bank_card_number = "4111111111111111"
        payment_settings.bank_recipient_name = "Test Owner"
        master_id, client_id, service_id = master.id, client.id, service.id
        await session.commit()

    entered_slot_check = asyncio.Event()
    release_slot_check = asyncio.Event()

    async def slow_slot_check(*args, **kwargs):
        entered_slot_check.set()
        await release_slot_check.wait()
        return True

    async def book() -> None:
        async with sessions() as session:
            await BookingService(session).create_hold_booking(
                master_id=master_id,
                user_id=client_id,
                service_id=service_id,
                start_time=datetime.now(timezone.utc) + timedelta(days=2),
            )
            await session.commit()

    async def suspend() -> None:
        async with sessions() as session:
            await SubscriptionService(session).suspend_master(master_id, "security review")
            await session.commit()

    with patch.object(SlotEngine, "is_slot_available", side_effect=slow_slot_check):
        booking_task = asyncio.create_task(book())
        try:
            await asyncio.wait_for(entered_slot_check.wait(), timeout=5)
            suspension_task = asyncio.create_task(suspend())
            await asyncio.sleep(0.2)
            assert not suspension_task.done(), "suspension must wait for booking's Master lock"
        finally:
            release_slot_check.set()
        await asyncio.wait_for(booking_task, timeout=10)
        await asyncio.wait_for(suspension_task, timeout=10)

    async with sessions() as session:
        assert (await session.get(Master, master_id)).subscription_status == SubscriptionStatus.SUSPENDED
        count = (await session.scalar(select(func.count(Appointment.id)).where(
            Appointment.master_id == master_id
        )))
        assert count == 1
        with pytest.raises(SubscriptionExpiredError):
            await BookingService(session).create_hold_booking(
                master_id=master_id,
                user_id=client_id,
                service_id=service_id,
                start_time=datetime.now(timezone.utc) + timedelta(days=3),
            )
