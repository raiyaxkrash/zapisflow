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
from app.database.models.user import User
from app.services.billing.yookassa_client import YooKassaClient, YooKassaGatewayError, YooKassaPayment
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


@dataclass(frozen=True)
class PaymentCheckResult:
    status: str  # "SUCCEEDED", "ALREADY_CONFIRMED", "PENDING", "CANCELLED", "GATEWAY_ERROR"
    payment_id: int
    plan_name: str
    plan_code: str
    period_days: int
    amount: Decimal
    currency: str
    paid_until: datetime | None = None
    confirmation_url: str | None = None
    error_message: str | None = None


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

    async def open_direct_checkout(
        self, *, actor_user_id: int, master_id: int, plan_code: str
    ) -> tuple[CheckoutOrder, CheckoutRedirect]:
        """Reuse a pending local order; create a provider redirect after DB commit."""
        if settings.payment_provider.lower() != "yookassa_web":
            raise SubscriptionError("Оплата сейчас недоступна")
        if settings.yookassa_fiscal_mode != "self_employed":
            raise SubscriptionError("Прямая оплата недоступна для текущего режима чеков")
        for _ in range(2):
            async with self.session_maker.begin() as session:
                master = await session.scalar(
                    select(Master).where(Master.id == master_id).with_for_update()
                )
                if master is None or master.owner_user_id != actor_user_id:
                    raise BillingIDORViolationError("Доступ к проекту запрещён")
                owner = await session.get(User, actor_user_id)
                if owner is None or not settings.can_use_yookassa_test_checkout(owner.telegram_id):
                    raise SubscriptionError("Тестовая оплата для этого аккаунта недоступна")
                if master.subscription_status == SubscriptionStatus.SUSPENDED:
                    raise SubscriptionError("Подписка заблокирована; обратитесь в поддержку")
                if not plan_code or len(plan_code) > 32:
                    raise PlanNotFoundError("Тариф не найден")
                plan = await session.scalar(select(SubscriptionPlan).where(
                    SubscriptionPlan.code == plan_code,
                    SubscriptionPlan.is_active.is_(True),
                ))
                if plan is None:
                    raise PlanNotFoundError("Тариф не найден")
                pending = await session.scalar(
                    select(SubscriptionPayment).where(
                        SubscriptionPayment.master_id == master_id,
                        SubscriptionPayment.plan_id == plan.id,
                        SubscriptionPayment.provider == PROVIDER_CODE,
                        SubscriptionPayment.status == "PENDING",
                    ).order_by(SubscriptionPayment.id.desc()).limit(1)
                )
                if pending is None:
                    order = await self.create_order_in_session(
                        session, actor_user_id=actor_user_id,
                        master_id=master_id, plan_code=plan_code,
                    )
                else:
                    if (
                        pending.checkout_ref is None or pending.amount <= 0
                        or pending.currency != "RUB" or pending.period_days <= 0
                    ):
                        raise SubscriptionError("Платёж требует проверки поддержкой")
                    order = CheckoutOrder(
                        pending.checkout_ref, pending.id, plan.id, plan.name,
                        pending.amount, pending.currency, pending.period_days,
                    )
            redirect = await self.start_checkout(
                checkout_ref=order.checkout_ref, actor_user_id=actor_user_id,
            )
            if redirect.status != "CANCELED":
                return order, redirect
        raise SubscriptionError("Не удалось создать новый платёж")

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
        owner = await session.get(User, actor_user_id)
        if owner is None or not settings.can_use_yookassa_test_checkout(owner.telegram_id):
            raise SubscriptionError("Тестовая оплата для этого аккаунта недоступна")
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
            owner = await session.get(User, actor_user_id)
            if owner is None or not settings.can_use_yookassa_test_checkout(owner.telegram_id):
                raise SubscriptionError("Тестовая оплата для этого аккаунта недоступна")
            if payment.status != "PENDING":
                raise SubscriptionError("Платёж уже завершён")
            if master.subscription_status == SubscriptionStatus.SUSPENDED:
                raise SubscriptionError("Подписка заблокирована; обратитесь в поддержку")
            amount, currency = payment.amount, payment.currency
            created_at = payment.created_at
            provider_id = payment.provider_payment_id
            local_payment_id = payment.id
            local_plan_id = payment.plan_id
            plan = await session.get(SubscriptionPlan, payment.plan_id)
            if plan is None or not plan.is_active:
                raise SubscriptionError("Тариф платежа не найден")
            description = f"{plan.name} — подписка на {payment.period_days} дней"

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
                payment_id=local_payment_id,
                user_id=actor_user_id,
                plan_id=local_plan_id,
            )
        else:
            remote = await self.client.get_payment(provider_id)
        self._verify_remote(
            remote, checkout_ref, amount, currency,
            payment_id=local_payment_id, user_id=actor_user_id, plan_id=local_plan_id,
        )
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
            owner = await session.get(User, actor_user_id)
            if owner is None or not settings.can_use_yookassa_test_checkout(owner.telegram_id):
                raise SubscriptionError("Тестовая оплата для этого аккаунта недоступна")
            if master.subscription_status == SubscriptionStatus.SUSPENDED:
                raise SubscriptionError("Подписка заблокирована; обратитесь в поддержку")
            plan = await session.get(SubscriptionPlan, payment.plan_id)
            if plan is None or not plan.is_active:
                raise SubscriptionError("Тариф платежа не найден")
            # Amount and term use the committed snapshot. Catalog price edits
            # cannot change an already-created provider payment.
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
            master = await session.get(Master, payment.master_id)
            if master is None:
                raise SubscriptionError("Проект платежа не найден")
            self._verify_remote(
                remote, checkout_ref, payment.amount, payment.currency,
                payment_id=payment.id, user_id=master.owner_user_id, plan_id=payment.plan_id,
            )
            if payment.provider_payment_id.startswith(UNCREATED_PREFIX):
                payment.provider_payment_id = remote.id
                await session.flush()
            elif payment.provider_payment_id != remote.id:
                raise SubscriptionError("Конфликт идентификатора платежа")

            if remote.status == "succeeded" and remote.paid:
                ok = await SubscriptionService(session).process_successful_payment(
                    PROVIDER_CODE, remote.id
                )
                try:
                    from app.services.subscription_notification_service import SubscriptionNotificationService
                    await SubscriptionNotificationService(session).send_payment_success_notification(
                        payment.master_id, payment.id
                    )
                except Exception:
                    pass
                return ok
            if remote.status == "canceled" and payment.status == "PENDING":
                payment.status = "CANCELLED"
            return False

    async def check_payment(
        self,
        *,
        payment_id: int,
        actor_user_id: int,
    ) -> PaymentCheckResult:
        """Authenticate caller, verify local order and remote YooKassa status, and activate subscription if succeeded."""
        async with self.session_maker() as session:
            payment = await session.get(SubscriptionPayment, payment_id)
            if payment is None or payment.provider != PROVIDER_CODE:
                raise SubscriptionError("Платёж не найден")
            master = await session.get(Master, payment.master_id)
            if master is None or master.owner_user_id != actor_user_id:
                raise BillingIDORViolationError("Доступ к проекту запрещён")
            owner = await session.get(User, actor_user_id)
            if owner is None or not settings.can_use_yookassa_test_checkout(owner.telegram_id):
                raise SubscriptionError("Тестовая оплата для этого аккаунта недоступна")
            if master.subscription_status == SubscriptionStatus.SUSPENDED:
                raise SubscriptionError("Подписка заблокирована; обратитесь в поддержку")
            plan = await session.get(SubscriptionPlan, payment.plan_id)
            if plan is None:
                raise SubscriptionError("Тариф платежа не найден")

            # Check snapshot properties
            if (
                payment.currency != "RUB"
                or payment.amount <= 0
                or payment.period_days <= 0
            ):
                raise SubscriptionError("Параметры платежа некорректны")

            # Fast path if already confirmed
            if payment.status == "SUCCEEDED":
                return PaymentCheckResult(
                    status="ALREADY_CONFIRMED",
                    payment_id=payment.id,
                    plan_name=plan.name,
                    plan_code=plan.code,
                    period_days=payment.period_days,
                    amount=payment.amount,
                    currency=payment.currency,
                    paid_until=master.paid_until,
                )

            # If already cancelled locally
            if payment.status == "CANCELLED":
                return PaymentCheckResult(
                    status="CANCELLED",
                    payment_id=payment.id,
                    plan_name=plan.name,
                    plan_code=plan.code,
                    period_days=payment.period_days,
                    amount=payment.amount,
                    currency=payment.currency,
                )

            provider_payment_id = payment.provider_payment_id
            checkout_ref = payment.checkout_ref
            amount = payment.amount
            currency = payment.currency
            plan_name = plan.name
            plan_code = plan.code
            period_days = payment.period_days
            local_plan_id = payment.plan_id

            if provider_payment_id.startswith(UNCREATED_PREFIX) or checkout_ref is None:
                return PaymentCheckResult(
                    status="PENDING",
                    payment_id=payment.id,
                    plan_name=plan_name,
                    plan_code=plan_code,
                    period_days=period_days,
                    amount=amount,
                    currency=currency,
                )

        # Call YooKassa outside DB transaction to prevent holding locks during remote HTTP call
        try:
            remote = await self.client.get_payment(provider_payment_id)
        except YooKassaGatewayError as exc:
            # Do NOT change local payment status to FAILED or CANCELLED on temporary gateway error!
            return PaymentCheckResult(
                status="GATEWAY_ERROR",
                payment_id=payment_id,
                plan_name=plan_name,
                plan_code=plan_code,
                period_days=period_days,
                amount=amount,
                currency=currency,
                error_message=str(exc),
            )

        # Verify remote response matches local order
        self._verify_remote(
            remote,
            checkout_ref,
            amount,
            currency,
            payment_id=payment_id,
            user_id=actor_user_id,
            plan_id=local_plan_id,
        )

        async with self.session_maker.begin() as session:
            payment = await session.scalar(
                select(SubscriptionPayment)
                .where(SubscriptionPayment.id == payment_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            if payment is None:
                raise SubscriptionError("Локальный платёж не найден")
            master = await session.get(Master, payment.master_id)
            if master is None:
                raise SubscriptionError("Проект платежа не найден")

            # Check if concurrent webhook or reconciliation already succeeded
            if payment.status == "SUCCEEDED":
                return PaymentCheckResult(
                    status="ALREADY_CONFIRMED",
                    payment_id=payment.id,
                    plan_name=plan_name,
                    plan_code=plan_code,
                    period_days=period_days,
                    amount=amount,
                    currency=currency,
                    paid_until=master.paid_until,
                )

            if remote.status == "succeeded" and remote.paid:
                await SubscriptionService(session).process_successful_payment(
                    PROVIDER_CODE, remote.id
                )
                await session.refresh(master)
                try:
                    from app.services.subscription_notification_service import SubscriptionNotificationService
                    await SubscriptionNotificationService(session).send_payment_success_notification(
                        payment.master_id, payment.id
                    )
                except Exception:
                    pass
                return PaymentCheckResult(
                    status="SUCCEEDED",
                    payment_id=payment.id,
                    plan_name=plan_name,
                    plan_code=plan_code,
                    period_days=period_days,
                    amount=amount,
                    currency=currency,
                    paid_until=master.paid_until,
                )

            if remote.status == "canceled":
                if payment.status == "PENDING":
                    payment.status = "CANCELLED"
                return PaymentCheckResult(
                    status="CANCELLED",
                    payment_id=payment.id,
                    plan_name=plan_name,
                    plan_code=plan_code,
                    period_days=period_days,
                    amount=amount,
                    currency=currency,
                )

            return PaymentCheckResult(
                status="PENDING",
                payment_id=payment.id,
                plan_name=plan_name,
                plan_code=plan_code,
                period_days=period_days,
                amount=amount,
                currency=currency,
                confirmation_url=remote.confirmation_url,
            )

    @staticmethod
    def _verify_remote(
        remote: YooKassaPayment,
        checkout_ref: uuid.UUID,
        amount: Decimal,
        currency: str,
        *,
        payment_id: int,
        user_id: int,
        plan_id: int | None,
    ) -> None:
        if (
            remote.checkout_ref != str(checkout_ref)
            or remote.amount != amount
            or remote.currency != currency
        ):
            raise SubscriptionError("Параметры ЮKassa не совпадают с заказом")
        # Older website payments only carried checkout_ref. New direct payments
        # carry all three IDs and each supplied value must match DB truth.
        expected = {
            "payment_id": str(payment_id),
            "user_id": str(user_id),
            "plan_id": str(plan_id),
        }
        if any(
            key in remote.metadata and remote.metadata[key] != value
            for key, value in expected.items()
        ):
            raise SubscriptionError("Параметры ЮKassa не совпадают с заказом")
