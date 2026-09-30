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


class TokenCryptoError(AppException):
    """Base exception for token cryptographic operations."""

    pass


class TokenCryptoConfigError(TokenCryptoError):
    """Raised when encryption configuration or key length/format is invalid."""

    pass


class TokenDecryptionError(TokenCryptoError):
    """Raised when token decryption fails due to corrupted ciphertext, bad key, or tampered AAD."""

    pass


class BotRegistryError(AppException):
    """Base exception for BotRegistry runtime operations."""

    pass


class BotNotFoundError(BotRegistryError):
    """Raised when a BotInstance is not found in database."""

    pass


class BotDisabledError(BotRegistryError):
    """Raised when attempting to obtain a bot that is disabled."""

    pass


class BotUnavailableError(BotRegistryError):
    """Raised when a bot is in an error state and cannot be used."""

    pass


class BotProvisioningError(BotRegistryError):
    """Raised when a bot is still in provisioning state."""

    pass


class BotSetupRequiredError(BotRegistryError):
    """Raised when a bot requires setup before it can be used."""

    pass


class ProvisioningError(AppException):
    """Base exception for bot onboarding and provisioning failures."""

    pass


class InvalidBotTokenError(ProvisioningError):
    """Raised when Telegram API rejects a bot token as unauthorized or invalid."""

    pass


class ManagerTokenCollisionError(ProvisioningError):
    """Raised when a user attempts to connect the platform manager bot token."""

    pass


class DuplicateBotError(ProvisioningError):
    """Raised when a bot with the given Telegram Bot ID is already registered."""

    pass


class ProvisioningWebhookError(ProvisioningError):
    """Raised when Telegram setWebhook fails during bot onboarding."""

    pass


class TokenRotationBotMismatchError(ProvisioningError):
    """Raised when rotating token to a different Telegram Bot ID."""

    pass


class TelegramGatewayError(ProvisioningError):
    """Raised when Telegram Bot API call encounters an unexpected error."""

    pass


class TelegramGatewayNetworkError(TelegramGatewayError):
    """Raised on network timeouts or connectivity failures with Telegram API."""

    pass


class MasterNotReadyError(AppException):
    """Raised when a master cannot be activated because onboarding checklist is incomplete."""

    def __init__(self, message: str, missing_items: list[str]) -> None:
        super().__init__(message)
        self.missing_items = missing_items
