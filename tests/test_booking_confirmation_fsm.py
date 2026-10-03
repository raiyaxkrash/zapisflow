"""Real Redis/PostgreSQL routing. Set TEST_REDIS_URL and TEST_DATABASE_URL."""

import copy
import asyncio
import os
from datetime import datetime, timedelta, timezone, time
from decimal import Decimal
from uuid import uuid4
from unittest.mock import AsyncMock, patch
from types import SimpleNamespace

import pytest
from aiogram import Bot, Dispatcher
from aiogram.client.session.base import BaseSession
from aiogram.fsm.storage.base import DefaultKeyBuilder
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.redis import RedisStorage
from aiogram.types import Update, Message
from redis.asyncio import Redis
from sqlalchemy import select, func, delete
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.bot.handlers.client import client_router
from app.bot.keyboards.client.callbacks import MenuCallback, ServiceCallback, CalendarNavCallback, TimeSlotCallback, BookingActionCallback
from app.bot.middlewares import TenantContextMiddleware, UserContextMiddleware
from app.bot.states.client import ClientBookingSG
from app.config.settings import settings
from app.database.models import User, Master, BotInstance, MasterSettings, Service
from app.database.models.master import MasterStatus, SubscriptionStatus, BotInstanceStatus
from app.database.models.service import DepositType
from app.database.models.staff import StaffMember
from app.database.models.schedule import ScheduleTemplate
from app.database.models.appointment import Appointment, AppointmentStatus
from app.database.models.payment import Payment
from app.database.models.telegram_outbox import TelegramOutbox
from app.database.models.processed_update import ProcessedWebhookUpdate
from app.bot.handlers.client.booking import cb_agree_policy
from app.bot.keyboards.client.callbacks import PolicyAgreementCallback
from app.bot.middlewares.db_session import DbSessionMiddleware
from tests.conftest import requires_postgres
from tests.test_webhook_business_replay import _middleware_with_savepoints


class TelegramStub(BaseSession):
    """Only Telegram transport is replaced; handlers/services/storage are real."""
    def __init__(self):
        super().__init__()
        self.calls = []

    async def close(self):
        pass

    async def stream_content(self, *args, **kwargs):
        if False:
            yield b""

    async def make_request(self, bot, method, timeout=None):
        self.calls.append(method)
        if method.__api_method__ == "answerCallbackQuery":
            return True
        return Message(message_id=777, date=datetime.now(timezone.utc),
                       chat={"id": method.chat_id, "type": "private"}, text=method.text)


@requires_postgres
@pytest.mark.asyncio
@pytest.mark.parametrize("deposit", [Decimal("100"), Decimal("0")])
async def test_full_booking_redis_confirmation_double_click(pg_session, monkeypatch, deposit):
    redis_url = os.environ.get("TEST_REDIS_URL")
    if not redis_url:
        pytest.fail("TEST_REDIS_URL is required for the booking Redis regression")
    redis = Redis.from_url(redis_url)
    await redis.ping()
    storage = RedisStorage(redis, key_builder=DefaultKeyBuilder(
        prefix=f"test-booking-{uuid4().hex}", with_bot_id=True, with_destiny=True))
    dp = Dispatcher(storage=storage)
    dp.update.outer_middleware(_middleware_with_savepoints(pg_session, monkeypatch))
    dp.update.outer_middleware(TenantContextMiddleware())
    dp.update.middleware(UserContextMiddleware())
    dp.include_router(copy.deepcopy(client_router))
    monkeypatch.setattr(settings, "app_mode", "webhook")
    suffix = uuid4().int % 100_000_000
    owner = User(telegram_id=10_000_000_000 + suffix, first_name="Owner")
    client = User(telegram_id=11_000_000_000 + suffix, first_name="Client", phone="+79991234567")
    pg_session.add_all([owner, client])
    await pg_session.flush()
    instances = []
    slot = (datetime.now(timezone.utc) + timedelta(days=2)).replace(hour=12, minute=0, second=0, microsecond=0)
    for index in range(2):
        master = Master(owner_user_id=owner.id, display_name="FSM test", status=MasterStatus.ACTIVE,
                        subscription_status=SubscriptionStatus.ACTIVE, paid_until=slot + timedelta(days=10))
        pg_session.add(master)
        await pg_session.flush()
        service = Service(master_id=master.id, title="Cut", duration_min=60, price=Decimal("1000"),
                          deposit_type=DepositType.FIXED, deposit_value=deposit, is_active=True)
        staff = StaffMember(master_id=master.id, user_id=owner.id, display_name="Owner", is_active=True)
        instance = BotInstance(master_id=master.id, telegram_bot_id=12_000_000_000 + suffix + index,
                               status=BotInstanceStatus.ACTIVE, is_current=True)
        pg_session.add_all([service, staff, instance, MasterSettings(master_id=master.id,
            bank_name="Bank", bank_card_number="4111111111111111", bank_recipient_name="Owner")])
        await pg_session.flush()
        pg_session.add(ScheduleTemplate(master_id=master.id, staff_id=staff.id, day_of_week=slot.weekday(),
                                        is_day_off=False, work_start=time(9), work_end=time(18)))
        instances.append((instance, service, staff))
    await pg_session.flush()
    other_context = FSMContext(storage, StorageKey(bot_id=instances[1][0].telegram_bot_id,
        chat_id=client.telegram_id, user_id=client.telegram_id))
    await other_context.set_data({"service_id": instances[1][1].id, "isolation_marker": True})
    transports = []
    try:
        for instance, service, staff in instances:
            transport = TelegramStub()
            transports.append(transport)
            bot = Bot(token=f"{instance.telegram_bot_id}:" + "A" * 35, session=transport)
            context = dp.fsm.get_context(bot=bot, chat_id=client.telegram_id, user_id=client.telegram_id)
            counter = 0

            async def feed(payload=None):
                nonlocal counter
                counter += 1
                message = {"message_id": 777, "date": int(datetime.now(timezone.utc).timestamp()),
                           "chat": {"id": client.telegram_id, "type": "private"}, "text": "/start"}
                user = {"id": client.telegram_id, "is_bot": False, "first_name": "Client"}
                if payload is None:
                    message["from"] = user
                    raw = {"update_id": counter, "message": message}
                else:
                    raw = {"update_id": counter, "callback_query": {"id": str(counter), "from": user,
                           "chat_instance": "test", "message": message, "data": payload}}
                await dp.feed_update(bot, Update.model_validate(raw), bot_instance=instance,
                                     master_id=instance.master_id, webhook_update_scope=f"tenant:{instance.id}")

            await feed()
            await feed(MenuCallback(action="book").pack())
            await feed(ServiceCallback(action="select", service_id=service.id).pack())
            assert (await context.get_data())["service_id"] == service.id
            await feed(CalendarNavCallback(action="select_day", year=slot.year, month=slot.month, day=slot.day).pack())
            assert await context.get_state() == ClientBookingSG.choosing_time.state
            assert (await context.get_data())["service_id"] == service.id
            await feed(TimeSlotCallback(service_id=service.id, timestamp=int(slot.timestamp())).pack())
            assert await context.get_state() == ClientBookingSG.confirming_policy.state
            assert (await context.get_data())["slot_timestamp"] == int(slot.timestamp())
            assert await redis.ttl(storage.key_builder.build(context.key, "data")) == -1
            policy_payload = transport.calls[-2].reply_markup.inline_keyboard[0][0].callback_data
            await feed(policy_payload)
            assert await context.get_data() == {}
            # Outbox deliberately hasn't sent the new screen yet. A second click
            # has a different update ID and must use PostgreSQL, not cleared FSM.
            await feed(policy_payload)
            answers = [c.text for c in transport.calls if c.__api_method__ == "answerCallbackQuery"]
            assert "Запись уже создана" in answers[-1]
            assert not any("Сессия истекла" in (a or "") for a in answers)
            assert await pg_session.scalar(select(func.count(Appointment.id)).where(Appointment.master_id == instance.master_id)) == 1
            assert await pg_session.scalar(select(func.count(Payment.id)).where(Payment.master_id == instance.master_id)) == (1 if deposit else 0)
            row = await pg_session.scalar(select(Appointment).where(Appointment.master_id == instance.master_id))
            assert row.status == (AppointmentStatus.WAITING_PAYMENT if deposit else AppointmentStatus.CONFIRMED)
            assert await pg_session.scalar(select(func.count(TelegramOutbox.id)).where(TelegramOutbox.master_id == instance.master_id)) == 1
            # Reusing the same Telegram message for a new policy must get a new
            # receipt identity; replaying the old policy must leave new FSM intact.
            await feed(MenuCallback(action="book").pack())
            await feed(ServiceCallback(action="select", service_id=service.id).pack())
            await feed(TimeSlotCallback(service_id=service.id, timestamp=int((slot + timedelta(minutes=90)).timestamp())).pack())
            next_policy_payload = transport.calls[-2].reply_markup.inline_keyboard[0][0].callback_data
            assert next_policy_payload != policy_payload
            next_data = await context.get_data()
            await feed(policy_payload)
            assert await context.get_data() == next_data
            await feed(next_policy_payload)
            assert await pg_session.scalar(select(func.count(Appointment.id)).where(Appointment.master_id == instance.master_id)) == 2
            if instance.id == instances[0][0].id:
                assert (await other_context.get_data())["isolation_marker"] is True
    finally:
        keys = await redis.keys(storage.key_builder.prefix + ":*")
        if keys:
            await redis.delete(*keys)
        await storage.close()


@pytest.mark.asyncio
async def test_production_webhook_fsm_redis_failure_is_fatal(monkeypatch):
    from app.bot.bot_instance import create_dispatcher
    monkeypatch.setattr(settings, "app_env", "production")
    monkeypatch.setattr(settings, "app_mode", "webhook")
    with patch("app.bot.bot_instance.Redis.from_url") as factory:
        factory.return_value.ping = AsyncMock(side_effect=ConnectionError("unavailable"))
        with pytest.raises(RuntimeError, match="Redis FSM storage is required"):
            await create_dispatcher()


@pytest.mark.asyncio
@pytest.mark.parametrize("reminder_interval", [30, 300])
async def test_interactive_outbox_does_not_wait_for_reminder_cycle(monkeypatch, reminder_interval):
    from app.scheduler.scheduler import setup_scheduler
    monkeypatch.setattr(settings, "reminder_delivery_interval_seconds", reminder_interval)
    monkeypatch.setattr(settings, "telegram_outbox_poll_interval_seconds", 2)
    scheduler = setup_scheduler()
    job = scheduler._scheduler.get_job("dispatch_telegram_outbox")
    assert job.trigger.interval.total_seconds() == 2
    assert job.max_instances == 1
    assert job.coalesce is True


@requires_postgres
@pytest.mark.asyncio
async def test_parallel_confirmation_distinct_updates_one_booking(pg_engine, monkeypatch):
    """Two process-like sessions contend on a policy, not on one update ID."""
    sessions = async_sessionmaker(pg_engine, expire_on_commit=False)
    suffix = uuid4().int % 100_000_000
    slot = (datetime.now(timezone.utc) + timedelta(days=2)).replace(hour=12, minute=0, second=0, microsecond=0)
    async with sessions.begin() as session:
        owner = User(telegram_id=13_000_000_000 + suffix, first_name="Owner")
        client = User(telegram_id=14_000_000_000 + suffix, first_name="Client", phone="+79991234567")
        session.add_all([owner, client])
        await session.flush()
        master = Master(owner_user_id=owner.id, display_name="Parallel", status=MasterStatus.ACTIVE,
                        subscription_status=SubscriptionStatus.ACTIVE, paid_until=slot + timedelta(days=10))
        session.add(master)
        await session.flush()
        service = Service(master_id=master.id, title="Cut", duration_min=60, price=Decimal("1000"),
                          deposit_type=DepositType.FIXED, deposit_value=Decimal("100"), is_active=True)
        staff = StaffMember(master_id=master.id, user_id=owner.id, display_name="Owner", is_active=True)
        instance = BotInstance(master_id=master.id, telegram_bot_id=15_000_000_000 + suffix,
                               status=BotInstanceStatus.ACTIVE, is_current=True)
        session.add_all([service, staff, instance, MasterSettings(master_id=master.id,
            bank_name="Bank", bank_card_number="4111111111111111", bank_recipient_name="Owner")])
        await session.flush()
        session.add(ScheduleTemplate(master_id=master.id, staff_id=staff.id, day_of_week=slot.weekday(),
                                     is_day_off=False, work_start=time(9), work_end=time(18)))
    redis = Redis.from_url(os.environ["TEST_REDIS_URL"])
    storage = RedisStorage(redis, key_builder=DefaultKeyBuilder(prefix=f"test-parallel-{uuid4().hex}",
                                                               with_bot_id=True, with_destiny=True))
    state = FSMContext(storage, StorageKey(bot_id=instance.telegram_bot_id, chat_id=client.telegram_id, user_id=client.telegram_id))
    nonce = uuid4().hex
    await state.set_data({"service_id": service.id, "staff_id": staff.id,
                          "slot_timestamp": int(slot.timestamp()), "confirmation_id": nonce})
    await state.set_state(ClientBookingSG.confirming_policy)
    callback = SimpleNamespace(message=SimpleNamespace(chat=SimpleNamespace(id=client.telegram_id), message_id=777),
                               from_user=SimpleNamespace(id=client.telegram_id), answer=AsyncMock())
    import app.bot.middlewares.db_session as middleware_module
    monkeypatch.setattr(middleware_module, "async_session_factory", sessions)
    scope = f"tenant:{instance.id}"

    async def handler(event, data):
        data["session"].info.update(trusted_master_id=master.id, bot_instance_id=instance.id)
        await cb_agree_policy(callback, state, client, data["session"], master.id,
                              PolicyAgreementCallback(confirmation_id=nonce))

    try:
        await asyncio.gather(*(DbSessionMiddleware()(handler, Update(update_id=i), {"webhook_update_scope": scope})
                               for i in (91, 92)))
        async with sessions() as session:
            for model in (Appointment, Payment, TelegramOutbox):
                assert await session.scalar(select(func.count()).select_from(model).where(model.master_id == master.id)) == 1
        assert any("Запись уже создана" in call.args[0] for call in callback.answer.await_args_list)
    finally:
        await state.clear()
        await storage.close()
        async with sessions.begin() as session:
            await session.execute(delete(ProcessedWebhookUpdate).where(ProcessedWebhookUpdate.scope == scope))
            for model in (TelegramOutbox, Payment, Appointment, StaffMember, BotInstance, Service, MasterSettings):
                await session.execute(delete(model).where(model.master_id == master.id))
            await session.execute(delete(Master).where(Master.id == master.id))
            await session.execute(delete(User).where(User.id.in_([owner.id, client.id])))
