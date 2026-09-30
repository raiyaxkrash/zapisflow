"""
Provider-neutral billing abstraction for SaaS subscription acquiring.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any, Dict, Optional

from app.database.models.subscription import SubscriptionPlan


@dataclass(frozen=True)
class PaymentIntent:
    """Representation of an initialized payment session with a billing provider."""

    provider: str
    provider_payment_id: str
    amount: Decimal
    currency: str
    payment_url: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None


@dataclass(frozen=True)
class CallbackVerificationResult:
    """Result of validating an inbound webhook callback from a billing provider."""

    is_valid: bool
    provider_payment_id: str
    status: str  # "SUCCEEDED", "FAILED", "PENDING", "CANCELLED"
    amount: Optional[Decimal] = None
    currency: Optional[str] = None
    paid_at: Optional[datetime] = None
    raw_event_type: Optional[str] = None
    sanitized_metadata: Optional[Dict[str, Any]] = None
    error_message: Optional[str] = None


class BillingProvider(ABC):
    """Abstract interface for billing acquiring providers."""

    @property
    @abstractmethod
    def provider_code(self) -> str:
        """Unique uppercase code identifying this provider (e.g. MANUAL, YOOKASSA)."""
        pass

    @abstractmethod
    async def create_payment_intent(
        self,
        master_id: int,
        plan: SubscriptionPlan,
        return_url: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> PaymentIntent:
        """Initialize a payment session and obtain a checkout link or payment id."""
        pass

    @abstractmethod
    async def verify_callback(
        self,
        payload: Dict[str, Any],
        signature: Optional[str] = None,
    ) -> CallbackVerificationResult:
        """Verify callback authenticity and extract normalized payment status."""
        pass

    @abstractmethod
    async def get_payment_status(self, provider_payment_id: str) -> CallbackVerificationResult:
        """Query the remote provider API for current status of a payment."""
        pass
