"""Failure injection for durable webhook business effects on PostgreSQL."""

import ast
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from aiogram.types import Update
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.bot.handlers.client.booking import cb_agree_policy
from app.bot.middlewares.db_session import DbSessionMiddleware
from app.database.models.appointment import Appointment, AppointmentStatus
from app.database.models.master import BotInstance, BotInstanceStatus, Master, MasterSettings, MasterStatus, SubscriptionStatus
from app.database.models.payment import Payment
from app.database.models.processed_update import ProcessedWebhookUpdate
from app.database.models.service import DepositType, Service
from app.database.models.subscription import SubscriptionPayment, SubscriptionPeriod, SubscriptionPlan
from app.database.models.telegram_outbox import TelegramOutbox
from app.database.models.user import User
from app.services.slot_engine import SlotEngine
from app.services.subscription_service import SubscriptionService
from tests.conftest import requires_postgres


def _middleware_with_savepoints(pg_session: AsyncSession, monkeypatch) -> DbSessionMiddleware:
    import app.bot.middlewares.db_session as db_middleware

    monkeypatch.setattr(
        db_middleware,
        "async_session_factory",
        async_sessionmaker(
            bind=pg_session.bind,
            expire_on_commit=False,
            join_transaction_mode="create_savepoint",
        ),
    )
    return DbSessionMiddleware()


@requires_postgres
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "deposit_value,expected_payment_count,expected_status",
    [
        (Decimal("100.00"), 1, AppointmentStatus.WAITING_PAYMENT),
        (Decimal("0.00"), 0, AppointmentStatus.CONFIRMED),
    ],
)
async def test_booking_callback_crash_and_replay_creates_one_appointment(
    pg_session: AsyncSession,
    monkeypatch,
    deposit_value: Decimal,
    expected_payment_count: int,
    expected_status: AppointmentStatus,
) -> None:
    """A callback's booking, outbox row and completed marker share one commit."""
    suffix = uuid4().int % 1_000_000_000
    owner = User(telegram_id=7_000_000_000 + suffix, first_name="Owner")
    client = User(telegram_id=8_000_000_000 + suffix, first_name="Client", phone="+79990001122")
    pg_session.add_all([owner, client])
    await pg_session.flush()
    master = Master(
        owner_user_id=owner.id,
        display_name=f"Booking replay {suffix}",
        status=MasterStatus.ACTIVE,
        subscription_status=SubscriptionStatus.ACTIVE,
        paid_until=datetime.now(timezone.utc) + timedelta(days=7),
    )
    pg_session.add(master)
    await pg_session.flush()
    if deposit_value > 0:
        pg_session.add(MasterSettings(
            master_id=master.id,
            bank_name="Test Bank",
            bank_card_number="4111111111111111",
            bank_recipient_name="Test Owner",
        ))
    service = Service(
        master_id=master.id,
        title="Replay service",
        duration_min=60,
        price=Decimal("1000.00"),
        deposit_type=DepositType.FIXED,
        deposit_value=deposit_value,
        is_active=True,
    )
    pg_session.add(service)
    bot_instance = BotInstance(
        master_id=master.id,
        telegram_bot_id=9_000_000_000 + suffix,
        status=BotInstanceStatus.ACTIVE,
        is_current=True,
    )
    pg_session.add(bot_instance)
    await pg_session.flush()

    middleware = _middleware_with_savepoints(pg_session, monkeypatch)
    callback = MagicMock()
    callback.message.chat.id = client.telegram_id
    callback.message.message_id = 777
    callback.from_user.id = client.telegram_id
    callback.answer = AsyncMock()
    slot = datetime.now(timezone.utc) + timedelta(days=2)
    state = MagicMock()
    state.get_data = AsyncMock(return_value={"service_id": service.id, "slot_timestamp": int(slot.timestamp())})
    state.clear = AsyncMock()
    update = Update(update_id=1_000_000_000 + suffix)
    scope = f"tenant:{bot_instance.id}"
    calls = 0

    async def handler(_event, data):
        nonlocal calls
        calls += 1
        session = data["session"]
        session.info["trusted_master_id"] = master.id
        session.info["bot_instance_id"] = bot_instance.id
        await cb_agree_policy(callback, state, client, session, master.id)

    async def crash_before_commit(event, data):
        await handler(event, data)
        raise RuntimeError("injected crash before business commit")

    with patch.object(SlotEngine, "is_slot_available", new_callable=AsyncMock, return_value=True):
        with pytest.raises(RuntimeError, match="injected crash"):
            await middleware(crash_before_commit, update, {"webhook_update_scope": scope})
        assert await pg_session.scalar(select(func.count(Appointment.id)).where(Appointment.master_id == master.id)) == 0

        await middleware(handler, update, {"webhook_update_scope": scope})
        await middleware(handler, update, {"webhook_update_scope": scope})

    assert calls == 2  # Failed first delivery and one successful delivery; replay skipped.
    assert await pg_session.scalar(select(func.count(Appointment.id)).where(Appointment.master_id == master.id)) == 1
    assert await pg_session.scalar(select(func.count(Payment.id)).where(Payment.master_id == master.id)) == expected_payment_count
    assert await pg_session.scalar(select(func.count(TelegramOutbox.id)).where(TelegramOutbox.master_id == master.id)) == 1
    appointment = await pg_session.scalar(select(Appointment).where(Appointment.master_id == master.id))
    assert appointment is not None and appointment.status == expected_status


@requires_postgres
@pytest.mark.asyncio
async def test_expired_booking_callback_is_acknowledged_and_deduplicated(
    pg_session: AsyncSession, monkeypatch
) -> None:
    """An expired subscription is a completed rejection, not a hanging webhook."""
    suffix = uuid4().int % 1_000_000_000
    owner = User(telegram_id=5_000_000_000 + suffix, first_name="Owner")
    client = User(telegram_id=6_000_000_000 + suffix, first_name="Client")
    pg_session.add_all([owner, client])
    await pg_session.flush()
    master = Master(
        owner_user_id=owner.id,
        display_name=f"Expired booking {suffix}",
        status=MasterStatus.ACTIVE,
        subscription_status=SubscriptionStatus.EXPIRED,
    )
    pg_session.add(master)
    await pg_session.flush()

    middleware = _middleware_with_savepoints(pg_session, monkeypatch)
    callback = MagicMock()
    callback.answer = AsyncMock()
    state = MagicMock()
    state.get_data = AsyncMock(
        return_value={"service_id": 123, "slot_timestamp": int((datetime.now(timezone.utc) + timedelta(days=1)).timestamp())}
    )
    state.clear = AsyncMock()
    update_id = 3_000_000_000 + suffix
    update = Update(update_id=update_id)
    scope = f"tenant:expired-{suffix}"
    calls = 0

    async def handler(_event, data):
        nonlocal calls
        calls += 1
        await cb_agree_policy(callback, state, client, data["session"], master.id)

    await middleware(handler, update, {"webhook_update_scope": scope})
    await middleware(handler, update, {"webhook_update_scope": scope})

    assert calls == 1
    callback.answer.assert_awaited_once()
    assert "временно недоступна" in callback.answer.await_args.args[0]
    assert callback.answer.await_args.kwargs["show_alert"] is True
    state.clear.assert_awaited_once()
    assert await pg_session.scalar(
        select(func.count(ProcessedWebhookUpdate.update_id)).where(
            ProcessedWebhookUpdate.scope == scope,
            ProcessedWebhookUpdate.update_id == update_id,
        )
    ) == 1
    assert await pg_session.scalar(
        select(func.count(Appointment.id)).where(Appointment.master_id == master.id)
    ) == 0
    assert await pg_session.scalar(
        select(func.count(Payment.id)).where(Payment.master_id == master.id)
    ) == 0


@requires_postgres
@pytest.mark.asyncio
async def test_subscription_confirmation_crash_and_replay_creates_one_period(pg_session: AsyncSession, monkeypatch) -> None:
    """Payment application rolls back before marker and remains idempotent on replay."""
    suffix = uuid4().hex
    owner = User(telegram_id=6_000_000_000 + int(suffix[:7], 16), first_name="Owner")
    pg_session.add(owner)
    await pg_session.flush()
    master = Master(owner_user_id=owner.id, display_name=f"Payment replay {suffix[:8]}", subscription_status=SubscriptionStatus.EXPIRED)
    plan = SubscriptionPlan(code=f"replay_{suffix[:16]}", name="Replay", price=Decimal("1200.00"), currency="RUB", period_days=30)
    pg_session.add_all([master, plan])
    await pg_session.flush()
    payment = SubscriptionPayment(
        master_id=master.id,
        plan_id=plan.id,
        provider="TEST",
        provider_payment_id=f"replay_{suffix}",
        amount=plan.price,
        currency="RUB",
        status="PENDING",
        period_days=30,
    )
    pg_session.add(payment)
    await pg_session.flush()

    middleware = _middleware_with_savepoints(pg_session, monkeypatch)
    update = Update(update_id=2_000_000_000 + int(suffix[:7], 16))

    async def handler(_event, data):
        await SubscriptionService(data["session"]).process_successful_payment(
            provider="TEST", provider_payment_id=payment.provider_payment_id,
        )

    async def crash_before_commit(event, data):
        await handler(event, data)
        raise RuntimeError("injected crash before subscription commit")

    with pytest.raises(RuntimeError, match="injected crash"):
        await middleware(crash_before_commit, update, {"webhook_update_scope": "manager"})
    await pg_session.refresh(payment)
    assert payment.status == "PENDING"
    assert await pg_session.scalar(select(func.count(SubscriptionPeriod.id)).where(SubscriptionPeriod.subscription_payment_id == payment.id)) == 0

    await middleware(handler, update, {"webhook_update_scope": "manager"})
    await middleware(handler, update, {"webhook_update_scope": "manager"})
    await pg_session.refresh(payment)
    await pg_session.refresh(master)
    assert payment.status == "SUCCEEDED"
    assert await pg_session.scalar(select(func.count(SubscriptionPeriod.id)).where(SubscriptionPeriod.subscription_payment_id == payment.id)) == 1
    assert timedelta(days=29) < master.paid_until - datetime.now(timezone.utc) < timedelta(days=31)


def test_telegram_handlers_do_not_commit_sessions() -> None:
    """Webhook transaction ownership stays in DbSessionMiddleware."""
    root = Path(__file__).resolve().parents[1]
    handler_files = list((root / "app" / "bot" / "handlers").rglob("*.py"))
    handler_files.append(root / "app" / "manager_bot" / "handlers.py")
    offenders = []
    for path in handler_files:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "commit"
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "session"
            ):
                offenders.append(f"{path.relative_to(root)}:{node.lineno}")
    assert offenders == []
