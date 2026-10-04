"""BillingProvider adapter over the existing credential-safe YooKassa REST client.

Creation requires a committed local order reference: never generate a new key
when retrying an uncertain request. Subscription activation belongs exclusively
to YooKassaCheckoutService -> SubscriptionService, not this adapter.
"""
from decimal import Decimal
from typing import Any
from uuid import UUID

from app.database.models.subscription import SubscriptionPlan
from app.services.billing.interface import (
    BillingProvider,
    CallbackVerificationResult,
    PaymentIntent,
)
from app.services.billing.yookassa_client import (
    YooKassaClient,
    YooKassaGatewayError,
    YooKassaPayment,
)


class YooKassaProvider(BillingProvider):
    def __init__(self, client: YooKassaClient) -> None:
        self.client = client

    @property
    def provider_code(self) -> str:
        return "YOOKASSA"

    async def create_checkout_payment(self, **kwargs: Any) -> YooKassaPayment:
        """Canonical durable checkout uses this same adapter with DB snapshots."""
        return await self.client.create_payment(**kwargs)

    async def create_payment_intent(self, master_id: int, plan: SubscriptionPlan,
                                    return_url: str | None = None,
                                    metadata: dict[str, Any] | None = None) -> PaymentIntent:
        data = metadata or {}
        # The orchestrator must commit this local order before calling the API.
        ref = str(UUID(str(data.get("checkout_ref", ""))))
        payment_id = int(data["payment_id"])
        actor_id = int(data["actor_user_id"])
        if payment_id <= 0 or actor_id <= 0 or master_id <= 0:
            raise ValueError("A persisted local order is required")
        remote = await self.create_checkout_payment(
            checkout_ref=ref, amount=Decimal(plan.price), currency=plan.currency,
            description=f"{plan.name} — подписка на {plan.period_days} дней",
            return_url=return_url or "", receipt=data.get("receipt"),
            payment_id=payment_id, user_id=actor_id, plan_id=plan.id,
            master_id=master_id, actor_user_id=actor_id, plan_code=plan.code,
        )
        expected = {"master_id": str(master_id), "actor_user_id": str(actor_id),
                    "plan_code": plan.code, "payment_id": str(payment_id)}
        if (remote.checkout_ref != ref or remote.amount != plan.price
                or remote.currency != plan.currency
                or any(remote.metadata.get(key) != value for key, value in expected.items())):
            raise YooKassaGatewayError("Параметры ЮKassa не совпадают с заказом")
        return PaymentIntent(self.provider_code, remote.id, remote.amount,
                             remote.currency, remote.confirmation_url, remote.metadata)

    async def get_payment_status(self, provider_payment_id: str) -> CallbackVerificationResult:
        remote = await self.client.get_payment(provider_payment_id)
        if remote.id != provider_payment_id:
            raise YooKassaGatewayError("Идентификатор ЮKassa не совпадает с запросом")
        status = {"pending": "PENDING", "waiting_for_capture": "PENDING",
                  "canceled": "CANCELLED", "succeeded": "SUCCEEDED"}.get(remote.status)
        valid = status is not None and (status != "SUCCEEDED" or remote.paid)
        return CallbackVerificationResult(
            is_valid=valid, provider_payment_id=remote.id,
            status=status if valid else "FAILED", amount=remote.amount,
            currency=remote.currency, sanitized_metadata=remote.metadata,
        )

    async def verify_callback(self, payload: dict[str, Any],
                              signature: str | None = None) -> CallbackVerificationResult:
        # Notification fields are untrusted hints. Fetch the authoritative object.
        obj = payload.get("object")
        payment_id = obj.get("id") if isinstance(obj, dict) else None
        if not isinstance(payment_id, str) or not payment_id:
            return CallbackVerificationResult(False, "", "FAILED",
                                              error_message="Некорректное уведомление")
        return await self.get_payment_status(payment_id)
