"""Tests for payment state machine transitions and proof verification workflows."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest

from app.database.models.appointment import Appointment, AppointmentStatus
from app.database.models.payment import MediaType, Payment, PaymentStatus
from app.services.exceptions import InvalidPaymentStatusError
from app.services.payment_service import PaymentService


def test_payment_allowed_transitions() -> None:
    payment = Payment(id=1, appointment_id=10, user_id=100, amount=Decimal("500.00"), status=PaymentStatus.PENDING)

    # Valid: PENDING -> SUBMITTED
    PaymentService._transition_payment(payment, PaymentStatus.SUBMITTED)
    assert payment.status == PaymentStatus.SUBMITTED

    # Valid: SUBMITTED -> CONFIRMED
    PaymentService._transition_payment(payment, PaymentStatus.CONFIRMED)
    assert payment.status == PaymentStatus.CONFIRMED

    # Valid: CONFIRMED -> RETAINED
    PaymentService._transition_payment(payment, PaymentStatus.RETAINED)
    assert payment.status == PaymentStatus.RETAINED

    # Invalid: RETAINED has no transitions
    with pytest.raises(InvalidPaymentStatusError):
        PaymentService._transition_payment(payment, PaymentStatus.PENDING)


def test_payment_rejection_cycle() -> None:
    payment = Payment(id=2, appointment_id=11, user_id=101, amount=Decimal("300.00"), status=PaymentStatus.PENDING)

    # PENDING -> SUBMITTED
    PaymentService._transition_payment(payment, PaymentStatus.SUBMITTED)
    assert payment.status == PaymentStatus.SUBMITTED

    # SUBMITTED -> REJECTED
    PaymentService._transition_payment(payment, PaymentStatus.REJECTED)
    assert payment.status == PaymentStatus.REJECTED

    # REJECTED -> SUBMITTED (client uploads new proof)
    PaymentService._transition_payment(payment, PaymentStatus.SUBMITTED)
    assert payment.status == PaymentStatus.SUBMITTED

    # SUBMITTED -> CONFIRMED
    PaymentService._transition_payment(payment, PaymentStatus.CONFIRMED)
    assert payment.status == PaymentStatus.CONFIRMED


@pytest.mark.asyncio
async def test_submit_payment_proof_workflow() -> None:
    session = AsyncMock()
    service = PaymentService(session)

    now = datetime.now(timezone.utc)
    appointment = Appointment(
        id=20,
        master_id=1,
        user_id=5,
        service_id=1,
        status=AppointmentStatus.WAITING_PAYMENT,
        start_time=now + timedelta(days=3),
        end_time=now + timedelta(days=3, hours=1),
        end_time_with_buffer=now + timedelta(days=3, hours=1, minutes=15),
        hold_until=now + timedelta(minutes=20),
        snapshot_service_title="Маникюр",
        snapshot_service_price=Decimal("1500.00"),
        snapshot_service_duration_min=60,
        snapshot_buffer_duration_min=15,
        snapshot_deposit_amount=Decimal("500.00"),
    )
    payment = Payment(
        id=30,
        master_id=1,
        appointment_id=20,
        user_id=5,
        amount=Decimal("500.00"),
        status=PaymentStatus.PENDING,
    )

    service.payment_repo = AsyncMock()
    service.payment_repo.get_by_appointment_id.return_value = payment
    service.appointment_repo = AsyncMock()
    service.appointment_repo.get_by_id_with_relations.return_value = appointment

    app_res, pay_res, proof_res = await service.submit_payment_proof(
        appointment_id=20,
        user_id=5,
        telegram_file_id="photo_123",
        telegram_file_unique_id="unique_123",
        media_type=MediaType.PHOTO,
        master_id=1,
    )

    assert pay_res.status == PaymentStatus.SUBMITTED
    assert app_res.status == AppointmentStatus.PAYMENT_PROOF_SENT
    assert session.flush.await_count >= 1
