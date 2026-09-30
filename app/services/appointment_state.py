"""Allowed appointment lifecycle transitions shared by booking and payment services."""

from datetime import datetime, timezone

from app.database.models.appointment import Appointment, AppointmentStatus
from app.services.exceptions import InvalidBookingStatusError


ALLOWED_TRANSITIONS = {
    AppointmentStatus.WAITING_PAYMENT: {
        AppointmentStatus.PAYMENT_PROOF_SENT,
        AppointmentStatus.CANCELLED_BY_CLIENT,
        AppointmentStatus.CANCELLED_BY_ADMIN,
        AppointmentStatus.EXPIRED,
    },
    AppointmentStatus.PAYMENT_PROOF_SENT: {
        AppointmentStatus.WAITING_PAYMENT,
        AppointmentStatus.CONFIRMED,
        AppointmentStatus.CANCELLED_BY_CLIENT,
        AppointmentStatus.CANCELLED_BY_ADMIN,
    },
    AppointmentStatus.CONFIRMED: {
        AppointmentStatus.COMPLETED,
        AppointmentStatus.NO_SHOW,
        AppointmentStatus.CANCELLED_BY_CLIENT,
        AppointmentStatus.CANCELLED_BY_ADMIN,
    },
    AppointmentStatus.CANCELLED_BY_CLIENT: set(),
    AppointmentStatus.CANCELLED_BY_ADMIN: set(),
    AppointmentStatus.COMPLETED: set(),
    AppointmentStatus.NO_SHOW: set(),
    AppointmentStatus.EXPIRED: set(),
}


def transition_appointment(
    appointment: Appointment, target: AppointmentStatus, *, now: datetime | None = None
) -> None:
    """Validate one transition before changing the in-memory row in a transaction."""
    if target not in ALLOWED_TRANSITIONS[appointment.status]:
        raise InvalidBookingStatusError(
            f"Переход записи {appointment.status.value} → {target.value} запрещён"
        )

    appointment.status = target
