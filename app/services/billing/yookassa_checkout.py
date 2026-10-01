"""Durable external YooKassa checkout for server-authorized project owners.

The Manager Bot issues a short-lived bearer capability for the website. The
browser cannot choose a buyer or master; the server resolves them from the
committed checkout session. A provider redirect is never proof of payment.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config.settings import settings
from app.database.models.audit import AuditLog
from app.database.models.master import Master, SubscriptionStatus
from app.database.models.subscription import SubscriptionPayment, SubscriptionPlan
from app.services.billing.yookassa_client import YooKassaClient, YooKassaPayment
from app.services.exceptions import BillingIDORViolationError, PlanNotFoundError, SubscriptionError
from app.services.subscription_service import SubscriptionService


PROVIDER_CODE = "YOOKASSA"
UNCREATED_PREFIX = "checkout:"
IDEMPOTENCY_RETRY_LIMIT = timedelta(hours=23)


@dataclass(frozen=True)
class CheckoutOrder:
    checkout_ref: uuid.UUID
    payment_id: int
    plan_id: int
    plan_name: str
    amount: Decimal
    currency: str
    period_days: int


@dataclass(frozen=True)
class CheckoutRedirect:
    checkout_ref: uuid.UUID
    confirmation_url: str | None
    status: str


class YooKassaCheckoutService:
    """Prepare, start, and reconcile one local order per external purchase."""

    def __init__(
        self,
        session_maker: async_sessionmaker[AsyncSession],
        client: YooKassaClient,
    ) -> None:
        self.session_maker = session_maker
        self.client = client

    async def create_order(
        self,
        *,
        actor_user_id: int,
        master_id: int,
        plan_code: str,
    ) -> CheckoutOrder:
        async with self.session_maker.begin() as session:
            return await self.create_order_in_session(
                session,
                actor_user_id=actor_user_id,
                master_id=master_id,
                plan_code=plan_code,
            )

    async def create_order_in_session(
        self,
        session: AsyncSession,
        *,
        actor_user_id: int,
        master_id: int,
        plan_code: str,
    ) -> CheckoutOrder:
        """Stage an order in the caller's transaction, without committing."""
        if settings.payment_provider.lower() != "yookassa_web":
            raise SubscriptionError("Оплата на сайте сейчас недоступна")
        if not plan_code or len(plan_code) > 32:
            raise PlanNotFoundError("Тариф не найден")
        master = await session.get(Master, master_id)
        if master is None or master.owner_user_id != actor_user_id:
            raise BillingIDORViolationError("Доступ к проекту запрещён")
        if master.subscription_status == SubscriptionStatus.SUSPENDED:
            raise SubscriptionError("Подписка заблокирована; обратитесь в поддержку")
        plan = await session.scalar(
            select(SubscriptionPlan).where(
                SubscriptionPlan.code == plan_code,
                SubscriptionPlan.is_active.is_(True),
            )
        )
        if plan is None:
            raise PlanNotFoundError("Тариф не найден")
        if (
            plan.currency != settings.payment_currency
            or plan.currency != "RUB"
            or plan.price <= 0
            or plan.period_days <= 0
        ):
            raise SubscriptionError("Параметры тарифа некорректны")

        checkout_ref = uuid.uuid4()
        payment = SubscriptionPayment(
            master_id=master.id,
            plan_id=plan.id,
            provider=PROVIDER_CODE,
            provider_payment_id=UNCREATED_PREFIX + checkout_ref.hex,
            checkout_ref=checkout_ref,
            amount=plan.price_rub,
            currency=plan.currency,
            status="PENDING",
            period_days=plan.duration_days,
        )
        session.add(payment)
        await session.flush()
        session.add(AuditLog(
            master_id=master.id,
            actor_user_id=actor_user_id,
            action="SUBSCRIPTION_PAYMENT_CREATED",
            entity_type="SubscriptionPayment",
            entity_id=payment.id,
            payload_after={
                "provider": PROVIDER_CODE,
                "plan_code": plan.code,
                "amount": str(payment.amount),
                "currency": payment.currency,
            },
        ))
        return CheckoutOrder(
            checkout_ref=checkout_ref,
            payment_id=payment.id,
            plan_id=plan.id,
            plan_name=plan.name,
            amount=payment.amount,
            currency=payment.currency,
            period_days=payment.period_days,
        )

    async def start_checkout(
        self,
        *,
        checkout_ref: uuid.UUID,
        actor_user_id: int,
        receipt: dict | None = None,
    ) -> CheckoutRedirect:
        """Call YooKassa after local order commit; reuse the same key on retry."""
        async with self.session_maker() as session:
            payment = await session.scalar(
                select(SubscriptionPayment).where(
                    SubscriptionPayment.checkout_ref == checkout_ref,
                    SubscriptionPayment.provider == PROVIDER_CODE,
                )
            )
            if payment is None:
                raise SubscriptionError("Платёж не найден")
            master = await session.get(Master, payment.master_id)
            if master is None or master.owner_user_id != actor_user_id:
                raise BillingIDORViolationError("Доступ к проекту запрещён")
            if payment.status != "PENDING":
                raise SubscriptionError("Платёж уже завершён")
            if master.subscription_status == SubscriptionStatus.SUSPENDED:
                raise SubscriptionError("Подписка заблокирована; обратитесь в поддержку")
            amount, currency = payment.amount, payment.currency
            created_at = payment.created_at
            provider_id = payment.provider_payment_id
            plan = await session.get(SubscriptionPlan, payment.plan_id)
            if plan is None:
                raise SubscriptionError("Тариф платежа не найден")
            description = f"{plan.name}: подписка на {payment.period_days} дней"

        if provider_id.startswith(UNCREATED_PREFIX):
            if created_at.tzinfo is None:
                created_at = created_at.replace(tzinfo=timezone.utc)
            if datetime.now(timezone.utc) - created_at >= IDEMPOTENCY_RETRY_LIMIT:
                # YooKassa's key expires after 24h. A second POST could charge twice.
                raise SubscriptionError("Время создания платежа истекло; обратитесь в поддержку")
            remote = await self.client.create_payment(
                checkout_ref=str(checkout_ref),
                amount=amount,
                currency=currency,
                description=description,
                return_url=settings.billing_return_url,
                receipt=receipt,
            )
        else:
            remote = await self.client.get_payment(provider_id)
        self._verify_remote(remote, checkout_ref, amount, currency)
        if remote.status in {"succeeded", "canceled"}:
            await self.reconcile(remote.id)
            return CheckoutRedirect(checkout_ref, None, remote.status.upper())
        if remote.status != "pending" or not remote.confirmation_url:
            raise SubscriptionError("ЮKassa не вернула страницу ожидающего платежа")

        async with self.session_maker.begin() as session:
            payment = await session.scalar(
                select(SubscriptionPayment)
                .where(SubscriptionPayment.checkout_ref == checkout_ref)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            if payment is None or payment.status != "PENDING":
                raise SubscriptionError("Платёж уже завершён")
            master = await session.get(Master, payment.master_id)
            if master is None or master.owner_user_id != actor_user_id:
                raise BillingIDORViolationError("Доступ к проекту запрещён")
            if master.subscription_status == SubscriptionStatus.SUSPENDED:
                raise SubscriptionError("Подписка заблокирована; обратитесь в поддержку")
            # The order's amount, term, and plan were validated and committed
            # before the provider request. A later catalog edit must not make
            # this already-created order unusable.
            if payment.provider_payment_id.startswith(UNCREATED_PREFIX):
                payment.provider_payment_id = remote.id
            elif payment.provider_payment_id != remote.id:
                raise SubscriptionError("Конфликт идентификатора платежа")
            await session.flush()

        return CheckoutRedirect(checkout_ref, remote.confirmation_url, "PENDING")

    async def reconcile(self, provider_payment_id: str) -> bool:
        """Verify provider truth over authenticated GET, then activate once in DB."""
        remote = await self.client.get_payment(provider_payment_id)
        if remote.id != provider_payment_id or not remote.checkout_ref:
            raise SubscriptionError("Платёж ЮKassa не связан с заказом")
        try:
            checkout_ref = uuid.UUID(remote.checkout_ref)
        except ValueError as exc:
            raise SubscriptionError("Некорректная связь платежа ЮKassa") from exc

        async with self.session_maker.begin() as session:
            payment = await session.scalar(
                select(SubscriptionPayment)
                .where(SubscriptionPayment.checkout_ref == checkout_ref)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            if payment is None or payment.provider != PROVIDER_CODE:
                raise SubscriptionError("Локальный платёж не найден")
            self._verify_remote(remote, checkout_ref, payment.amount, payment.currency)
            if payment.provider_payment_id.startswith(UNCREATED_PREFIX):
                payment.provider_payment_id = remote.id
                await session.flush()
            elif payment.provider_payment_id != remote.id:
                raise SubscriptionError("Конфликт идентификатора платежа")

            if remote.status == "succeeded" and remote.paid:
                return await SubscriptionService(session).process_successful_payment(
                    PROVIDER_CODE, remote.id
                )
            if remote.status == "canceled" and payment.status == "PENDING":
                payment.status = "CANCELLED"
            return False

    @staticmethod
    def _verify_remote(
        remote: YooKassaPayment,
        checkout_ref: uuid.UUID,
        amount: Decimal,
        currency: str,
    ) -> None:
        if (
            remote.checkout_ref != str(checkout_ref)
            or remote.amount != amount
            or remote.currency != currency
        ):
            raise SubscriptionError("Параметры ЮKassa не совпадают с заказом")
