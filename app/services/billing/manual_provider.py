"""
Manual / dev billing provider for administrative activations and automated testing.
"""

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, Optional
import uuid

from app.database.models.subscription import SubscriptionPlan
from app.services.billing.interface import (
    BillingProvider,
    CallbackVerificationResult,
    PaymentIntent,
)


class ManualBillingProvider(BillingProvider):
    """
    Simulated provider for local development, unit tests, and offline admin activations.
    Clearly designated as NOT a real acquiring integration.
    """

    @property
    def provider_code(self) -> str:
        return "MANUAL"

    async def create_payment_intent(
        self,
        master_id: int,
        plan: SubscriptionPlan,
        return_url: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> PaymentIntent:
        provider_payment_id = f"man_{uuid.uuid4().hex[:16]}"
        checkout_url = f"https://dev.beautybot.local/billing/mock-checkout/{provider_payment_id}"
        return PaymentIntent(
            provider=self.provider_code,
            provider_payment_id=provider_payment_id,
            amount=plan.price,
            currency=plan.currency,
            payment_url=checkout_url,
            metadata=metadata,
        )

    async def verify_callback(
        self,
        payload: Dict[str, Any],
        signature: Optional[str] = None,
    ) -> CallbackVerificationResult:
        """
        Verify manual callback.
        Expects payload with at least `provider_payment_id` and optional `status`.
        """
        pid = payload.get("provider_payment_id")
        if not pid:
            return CallbackVerificationResult(
                is_valid=False,
                provider_payment_id="",
                status="FAILED",
                error_message="Missing provider_payment_id in payload",
            )

        status = payload.get("status", "SUCCEEDED").upper()
        raw_amount = payload.get("amount")
        amount = Decimal(str(raw_amount)) if raw_amount is not None else None
        currency = payload.get("currency", "RUB")

        return CallbackVerificationResult(
            is_valid=True,
            provider_payment_id=pid,
            status=status,
            amount=amount,
            currency=currency,
            paid_at=datetime.now(timezone.utc) if status == "SUCCEEDED" else None,
            sanitized_metadata={"manual": True, "note": payload.get("note", "Manual confirmation")},
        )

    async def get_payment_status(self, provider_payment_id: str) -> CallbackVerificationResult:
        return CallbackVerificationResult(
            is_valid=True,
            provider_payment_id=provider_payment_id,
            status="SUCCEEDED",
            paid_at=datetime.now(timezone.utc),
        )
