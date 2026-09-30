"""
Domain business logic exceptions.
"""


class AppException(Exception):
    """
    Base exception for domain errors.
    """
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class SlotAlreadyBookedError(AppException):
    """
    Raised when a requested slot has already been booked or is held.
    """
    pass


class BookingNotFoundError(AppException):
    """
    Raised when an appointment is not found.
    """
    pass


class ServiceNotFoundError(AppException):
    """
    Raised when a service is not found or is archived.
    """
    pass


class UserNotFoundError(AppException):
    """
    Raised when a user profile is not found.
    """
    pass


class HoldExpiredError(AppException):
    """
    Raised when attempting an operation on an expired temporary hold.
    """
    pass


class InvalidBookingStatusError(AppException):
    """
    Raised when an operation is invalid for the appointment's current status.
    """
    pass


class ScheduleConflictError(AppException):
    """
    Raised when a schedule adjustment conflicts with active appointments.
    """
    pass


class PaymentNotFoundError(AppException):
    """
    Raised when a payment record is not found.
    """
    pass


class InvalidPaymentStatusError(AppException):
    """Raised when a payment decision violates the allowed state machine."""

    pass


class AccessDeniedError(AppException):
    """Raised when an operation is forbidden due to insufficient tenant administrative privileges."""

    pass
