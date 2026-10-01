"""Opaque, one-use bearer sessions for the external SaaS checkout page."""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import re
import secrets
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.database.models.checkout_session import CheckoutSession
from app.database.models.master import Master, SubscriptionStatus
from app.database.models.subscription import SubscriptionPayment, SubscriptionPlan
from app.services.billing.yookassa_checkout import CheckoutOrder, YooKassaCheckoutService
from app.services.exceptions import SubscriptionError


TOKEN_PATTERN = re.compile(r"[A-Za-z0-9_-]{43}\Z")
SESSION_TTL = timedelta(minutes=15)


@dataclass(frozen=True)
class CheckoutOffer:
    checkout_ref: uuid.UUID
    user_id: int
    master_id: int
    payment_id: int
    plan_name: str
    amount: Decimal
    currency: str
    period_days: int


def _token_hash(token: str) -> str:
    if not TOKEN_PATTERN.fullmatch(token):
        raise SubscriptionError("Ссылка на оплату недействительна")
    return hashlib.sha256(token.encode("ascii")).hexdigest()


def _receipt_email(value: str) -> str:
    email = value.strip().lower()
    if len(email) > 254 or email.count("@") != 1 or any(ord(char) > 127 or char.isspace() for char in email):
        raise SubscriptionError("Укажите корректный email для чека")
    local, domain = email.split("@")
    if not 1 <= len(local) <= 64 or not domain or "." not in domain or domain.startswith("."):
        raise SubscriptionError("Укажите корректный email для чека")
    if any(
        not part or len(part) > 63 or part.startswith("-") or part.endswith("-")
        for part in domain.split(".")
    ):
        raise SubscriptionError("Укажите корректный email для чека")
    if local.startswith(".") or local.endswith(".") or ".." in local:
        raise SubscriptionError("Укажите корректный email для чека")
    if not re.fullmatch(r"[A-Za-z0-9._%+-]+", local) or not re.fullmatch(r"[A-Za-z0-9.-]+", domain):
        raise SubscriptionError("Укажите корректный email для чека")
    return email


class CheckoutSessionService:
    def __init__(
        self,
        session_maker: async_sessionmaker[AsyncSession],
        checkout_service: YooKassaCheckoutService,
    ) -> None:
        self.session_maker = session_maker
        self.checkout_service = checkout_service

    async def issue(
        self,
        session: AsyncSession,
        *,
        actor_user_id: int,
        master_id: int,
        plan_code: str = "basic_monthly",
    ) -> tuple[str, CheckoutOrder]:
        """Create order and capability in the caller's business transaction."""
        order = await self.checkout_service.create_order_in_session(
            session,
            actor_user_id=actor_user_id,
            master_id=master_id,
            plan_code=plan_code,
        )
        token = secrets.token_urlsafe(32)
        session.add(CheckoutSession(
            token_hash=_token_hash(token),
            user_id=actor_user_id,
            master_id=master_id,
            payment_id=order.payment_id,
            plan_id=order.plan_id,
            status="ISSUED",
            expires_at=datetime.now(timezone.utc) + SESSION_TTL,
        ))
        await session.flush()
        return token, order

    async def inspect(self, token: str) -> CheckoutOffer:
        async with self.session_maker() as session:
            row = await session.scalar(
                select(CheckoutSession).where(CheckoutSession.token_hash == _token_hash(token))
            )
            return await self._validate(session, row)

    async def consume(self, token: str, email: str) -> tuple[CheckoutOffer, str]:
        """Atomically consume the capability before calling the external API."""
        valid_email = _receipt_email(email)
        async with self.session_maker.begin() as session:
            row = await session.scalar(
                select(CheckoutSession)
                .where(CheckoutSession.token_hash == _token_hash(token))
                .with_for_update()
            )
            offer = await self._validate(session, row)
            assert row is not None
            row.status = "USED"
            row.used_at = datetime.now(timezone.utc)
            row.receipt_email = valid_email
            await session.flush()
            return offer, valid_email

    @staticmethod
    async def _validate(session: AsyncSession, row: CheckoutSession | None) -> CheckoutOffer:
        now = datetime.now(timezone.utc)
        if row is None or row.status != "ISSUED" or row.used_at is not None or row.expires_at <= now:
            raise SubscriptionError("Ссылка на оплату истекла или уже использована")
        payment = await session.get(SubscriptionPayment, row.payment_id)
        master = await session.get(Master, row.master_id)
        plan = await session.get(SubscriptionPlan, row.plan_id)
        if (
            payment is None
            or master is None
            or plan is None
            or payment.master_id != row.master_id
            or payment.plan_id != row.plan_id
            or payment.status != "PENDING"
            or payment.provider != "YOOKASSA"
            or master.owner_user_id != row.user_id
            or master.subscription_status == SubscriptionStatus.SUSPENDED
            or not plan.is_active
        ):
            raise SubscriptionError("Этот заказ больше нельзя оплатить")
        if payment.checkout_ref is None or payment.amount <= 0 or payment.currency != "RUB":
            raise SubscriptionError("Параметры заказа некорректны")
        return CheckoutOffer(
            checkout_ref=payment.checkout_ref,
            user_id=row.user_id,
            master_id=row.master_id,
            payment_id=payment.id,
            plan_name=plan.name,
            amount=payment.amount,
            currency=payment.currency,
            period_days=payment.period_days,
        )
