"""Tenant isolation, rendering and persistence of structured master contacts."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.bot.handlers.admin.contacts_mgmt import (
    cb_admin_contact_field,
    cb_admin_contact_preview,
    msg_admin_contact_save,
)
from app.bot.handlers.client.about import cb_contact_call, cb_contact_master
from app.bot.keyboards.client import MenuCallback
from app.bot.keyboards.client.booking import get_appointment_detail_keyboard
from app.config.settings import settings as app_settings
from app.database.models.appointment import AppointmentStatus
from app.database.models.master import (
    BotInstance,
    BotInstanceStatus,
    Master,
    MasterAdmin,
    MasterAdminRole,
    MasterSettings,
    MasterStatus,
    SubscriptionStatus,
)
from app.database.models.telegram_outbox import TelegramOutbox
from app.database.models.user import User
from app.repositories.master_settings_repository import MasterSettingsRepository
from app.scheduler.jobs.telegram_outbox_worker import _Delivery, _send
from app.services.exceptions import AccessDeniedError
from app.services.master_contacts import (
    MasterContactsService,
    contact_phone_e164,
    render_contacts,
)
from tests.conftest import requires_postgres


def _buttons(markup) -> list[tuple[str, str | None, str | None]]:
    return [
        (button.text, button.url, button.callback_data)
        for row in markup.inline_keyboard
        for button in row
    ]


def _settings(**values: str | None) -> MasterSettings:
    return MasterSettings(master_id=1, **values)


async def _user(session: AsyncSession, name: str) -> User:
    telegram_id = 10**12 + (uuid4().int % 10**11)
    user = User(telegram_id=telegram_id, first_name=name)
    session.add(user)
    await session.flush()
    return user


async def _master(session: AsyncSession, owner: User, name: str) -> Master:
    master = Master(
        owner_user_id=owner.id,
        display_name=name,
        status=MasterStatus.ACTIVE,
        subscription_status=SubscriptionStatus.ACTIVE,
        timezone="Europe/Moscow",
    )
    session.add(master)
    await session.flush()
    session.add(MasterSettings(master_id=master.id))
    await session.flush()
    return master


@requires_postgres
@pytest.mark.asyncio
async def test_owner_edits_only_own_contacts_and_database_retains_values(pg_session: AsyncSession):
    owner_a = await _user(pg_session, "Owner A")
    owner_b = await _user(pg_session, "Owner B")
    master_a = await _master(pg_session, owner_a, "A")
    master_b = await _master(pg_session, owner_b, "B")
    master_a_id, master_b_id = master_a.id, master_b.id
    service = MasterContactsService(pg_session)

    await service.update_field(master_a.id, owner_a.id, "studio_phone", "+7 (999) 000-00-00")
    await service.update_field(master_a.id, owner_a.id, "studio_address", "Москва, ул. Ленина, 25")
    await pg_session.flush()
    pg_session.expire_all()

    a = await pg_session.get(MasterSettings, master_a_id)
    b = await pg_session.get(MasterSettings, master_b_id)
    assert a.studio_phone == "+7 (999) 000-00-00"
    assert a.studio_address == "Москва, ул. Ленина, 25"
    assert b.studio_phone is None
    assert b.studio_address is None


@requires_postgres
@pytest.mark.asyncio
async def test_contact_change_survives_commit_and_new_database_session(pg_engine: AsyncEngine):
    """A second PostgreSQL connection sees the owner's committed edit."""
    session_factory = async_sessionmaker(pg_engine, expire_on_commit=False)
    owner_id: int | None = None
    master_id: int | None = None
    try:
        async with session_factory() as writer:
            owner = await _user(writer, "Committed owner")
            master = await _master(writer, owner, "Committed master")
            owner_id, master_id = owner.id, master.id
            await MasterContactsService(writer).update_field(
                master_id, owner_id, "studio_address", "Москва, ул. Ленина, 25"
            )
            await writer.commit()

        async with session_factory() as reader:
            stored = await reader.get(MasterSettings, master_id)
            assert stored is not None
            assert stored.studio_address == "Москва, ул. Ленина, 25"
    finally:
        async with session_factory() as cleanup:
            if master_id is not None:
                await cleanup.execute(delete(Master).where(Master.id == master_id))
            if owner_id is not None:
                await cleanup.execute(delete(User).where(User.id == owner_id))
            await cleanup.commit()


@requires_postgres
@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("field", "value", "stored"),
    [
        ("studio_phone", "+7 (999) 000-00-00", "+7 (999) 000-00-00"),
        ("whatsapp_phone", "+7 999 123-45-67", "+7 999 123-45-67"),
        ("studio_address", "Москва, ул. Ленина, 25", "Москва, ул. Ленина, 25"),
        ("working_hours_text", "10:00–19:00", "10:00–19:00"),
        ("contacts_intro_text", "По вопросам записи пишите мне.", "По вопросам записи пишите мне."),
        ("telegram_username", "@master_name", "master_name"),
    ],
)
async def test_each_contact_field_is_structured_and_persisted(
    pg_session: AsyncSession, field: str, value: str, stored: str
):
    owner = await _user(pg_session, "Owner")
    master = await _master(pg_session, owner, "A")
    master_id = master.id
    owner_id = owner.id
    service = MasterContactsService(pg_session)

    await service.update_field(master_id, owner_id, field, value)
    await pg_session.flush()
    pg_session.expire_all()
    fresh = await pg_session.get(MasterSettings, master_id)
    assert getattr(fresh, field) == stored

    await service.update_field(master_id, owner_id, field, "/clear")
    await pg_session.flush()
    pg_session.expire_all()
    cleared = await pg_session.get(MasterSettings, master_id)
    assert getattr(cleared, field) is None


@requires_postgres
@pytest.mark.asyncio
async def test_active_admin_may_edit_contacts_but_not_other_tenant(pg_session: AsyncSession):
    owner_a = await _user(pg_session, "Owner A")
    owner_b = await _user(pg_session, "Owner B")
    admin_a = await _user(pg_session, "Admin A")
    master_a = await _master(pg_session, owner_a, "A")
    master_b = await _master(pg_session, owner_b, "B")
    pg_session.add(
        MasterAdmin(
            master_id=master_a.id,
            user_id=admin_a.id,
            role=MasterAdminRole.ADMIN,
            is_active=True,
        )
    )
    await pg_session.flush()
    service = MasterContactsService(pg_session)

    await service.update_field(master_a.id, admin_a.id, "whatsapp_phone", "+7 999 123-45-67")
    with pytest.raises(AccessDeniedError):
        await service.update_field(master_b.id, admin_a.id, "whatsapp_phone", "+7 999 000-00-00")
    assert (await pg_session.get(MasterSettings, master_a.id)).whatsapp_phone == "+7 999 123-45-67"
    assert (await pg_session.get(MasterSettings, master_b.id)).whatsapp_phone is None


@requires_postgres
@pytest.mark.asyncio
async def test_other_tenant_owner_and_regular_user_cannot_edit_contacts(pg_session: AsyncSession):
    owner_a = await _user(pg_session, "Owner A")
    owner_b = await _user(pg_session, "Owner B")
    stranger = await _user(pg_session, "Stranger")
    master_a = await _master(pg_session, owner_a, "A")
    await _master(pg_session, owner_b, "B")
    service = MasterContactsService(pg_session)

    for actor in (owner_b, stranger):
        with pytest.raises(AccessDeniedError):
            await service.update_field(master_a.id, actor.id, "contacts_intro_text", "Forged")
    assert (await pg_session.get(MasterSettings, master_a.id)).contacts_intro_text is None


@requires_postgres
@pytest.mark.asyncio
async def test_unknown_field_cannot_modify_settings_via_forged_callback(pg_session: AsyncSession):
    owner = await _user(pg_session, "Owner")
    master = await _master(pg_session, owner, "A")
    service = MasterContactsService(pg_session)

    with pytest.raises(ValueError):
        await service.update_field(master.id, owner.id, "bank_card_number", "4111111111111111")
    with pytest.raises(ValueError):
        await service.update_field(master.id, owner.id, "__class__", "forged")
    settings = await pg_session.get(MasterSettings, master.id)
    assert settings.bank_card_number is None

    callback = SimpleNamespace(
        data="adm_contact:field:bank_card_number",
        message=SimpleNamespace(edit_text=AsyncMock()),
        answer=AsyncMock(),
    )
    await cb_admin_contact_field(
        callback, SimpleNamespace(clear=AsyncMock()), pg_session, master.id, owner
    )
    callback.answer.assert_awaited_once_with("Неизвестное поле", show_alert=True)
    callback.message.edit_text.assert_not_awaited()


@requires_postgres
@pytest.mark.asyncio
async def test_client_contact_callback_uses_trusted_master_id(pg_session: AsyncSession):
    owner_a = await _user(pg_session, "Owner A")
    owner_b = await _user(pg_session, "Owner B")
    master_a = await _master(pg_session, owner_a, "A")
    master_b = await _master(pg_session, owner_b, "B")
    await MasterContactsService(pg_session).update_field(
        master_a.id, owner_a.id, "studio_phone", "+7 999 111-11-11"
    )
    await MasterContactsService(pg_session).update_field(
        master_b.id, owner_b.id, "studio_phone", "+7 999 222-22-22"
    )
    message = SimpleNamespace(photo=None, edit_text=AsyncMock())
    callback = SimpleNamespace(message=message, answer=AsyncMock())

    await cb_contact_master(callback, pg_session, master_a.id)

    text = message.edit_text.await_args.kwargs["text"]
    keyboard = message.edit_text.await_args.kwargs["reply_markup"]
    assert "+7 999 111-11-11" in text
    assert "+7 999 222-22-22" not in text
    callback.answer.assert_awaited_once()

    preview_message = SimpleNamespace(edit_text=AsyncMock())
    preview = SimpleNamespace(message=preview_message, answer=AsyncMock())
    await cb_admin_contact_preview(
        preview, SimpleNamespace(clear=AsyncMock()), pg_session, master_a.id, owner_a
    )
    assert preview_message.edit_text.await_args.kwargs == {
        "text": text,
        "reply_markup": keyboard,
    }

    unauthorized_preview = SimpleNamespace(message=SimpleNamespace(edit_text=AsyncMock()), answer=AsyncMock())
    with pytest.raises(AccessDeniedError):
        await cb_admin_contact_preview(
            unauthorized_preview, SimpleNamespace(clear=AsyncMock()), pg_session, master_a.id, owner_b
        )
    unauthorized_preview.message.edit_text.assert_not_awaited()


@requires_postgres
@pytest.mark.asyncio
async def test_admin_edit_message_keeps_commit_owned_by_middleware(pg_session: AsyncSession):
    owner = await _user(pg_session, "Owner")
    master = await _master(pg_session, owner, "A")
    state = SimpleNamespace(
        get_data=AsyncMock(return_value={"contact_field": "working_hours_text", "contact_master_id": master.id}),
        clear=AsyncMock(),
    )
    message = SimpleNamespace(text="10:00–19:00", answer=AsyncMock())

    await msg_admin_contact_save(message, state, pg_session, master.id, owner)

    assert (await pg_session.get(MasterSettings, master.id)).working_hours_text == "10:00–19:00"
    state.clear.assert_not_awaited()
    message.answer.assert_not_awaited()
    assert len(pg_session.info["post_commit"]) == 2


@requires_postgres
@pytest.mark.asyncio
async def test_stale_edit_state_from_other_tenant_cannot_write(pg_session: AsyncSession):
    owner_a = await _user(pg_session, "Owner A")
    owner_b = await _user(pg_session, "Owner B")
    master_a = await _master(pg_session, owner_a, "A")
    master_b = await _master(pg_session, owner_b, "B")
    state = SimpleNamespace(
        get_data=AsyncMock(return_value={"contact_field": "studio_address", "contact_master_id": master_b.id}),
        clear=AsyncMock(),
    )
    message = SimpleNamespace(text="Injected address", answer=AsyncMock())

    await msg_admin_contact_save(message, state, pg_session, master_a.id, owner_a)

    assert (await pg_session.get(MasterSettings, master_a.id)).studio_address is None
    assert (await pg_session.get(MasterSettings, master_b.id)).studio_address is None
    state.clear.assert_awaited_once()
    assert "устарело" in message.answer.await_args.args[0]


def test_empty_fields_are_hidden_from_client():
    text, markup = render_contacts(_settings())
    assert "📞 Контакты мастера" in text
    for label in ("Телефон", "WhatsApp", "Адрес", "Режим работы", "не указан"):
        assert label not in text
    assert all(button[0] == "🏠 Главное меню" for button in _buttons(markup))


@pytest.mark.parametrize(
    ("field", "value", "expected"),
    [
        ("studio_phone", "+7 (999) 000-00-00", "+7 (999) 000-00-00"),
        ("whatsapp_phone", "+7 999 123-45-67", "+7 999 123-45-67"),
        ("studio_address", "Москва, ул. Ленина, 25", "Москва, ул. Ленина, 25"),
        ("working_hours_text", "10:00–19:00", "10:00–19:00"),
    ],
)
def test_structured_contact_fields_render_separately(field: str, value: str, expected: str):
    text, _ = render_contacts(_settings(**{field: value}))
    assert expected in text


def test_intro_address_hours_and_phone_are_html_escaped():
    settings = _settings(
        contacts_intro_text='<b>hello & "world"</b>',
        studio_address='<a href="evil">Москва & область</a>',
        working_hours_text="<script>alert(1)</script>",
        studio_phone="<bad>",
    )
    text, markup = render_contacts(settings)

    assert "&lt;b&gt;hello &amp;" in text
    assert "&lt;a href=" in text
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in text
    assert "<script>" not in text
    assert not any(label == "📞 Позвонить" for label, _, _ in _buttons(markup))


def test_links_use_validated_fixed_hosts_and_encoded_address():
    settings = _settings(
        studio_phone="+7 (999) 000-00-00",
        whatsapp_phone="+7 999 123-45-67",
        studio_address="Москва, ул. Ленина, 25 & студия 4",
        telegram_username="master_name",
    )
    _, markup = render_contacts(settings)
    buttons = _buttons(markup)

    assert any(label == "📞 Позвонить" and callback == "contact:call" for label, _, callback in buttons)
    assert any(label == "💬 Написать в WhatsApp" and url == "https://wa.me/79991234567" for label, url, _ in buttons)
    assert any(label == "✈️ Написать в Telegram" and url == "https://t.me/master_name" for label, url, _ in buttons)
    assert any(label == "📍 Открыть адрес" and url.startswith("https://www.google.com/maps/search/?api=1&query=") and "%26" in url for label, url, _ in buttons)


@pytest.mark.parametrize("bad_phone", ["123", "javascript:alert(1)", "+7 <x> 123", "https://evil.test"])
def test_invalid_phone_never_becomes_call_target(bad_phone: str):
    assert contact_phone_e164(bad_phone) is None
    _, markup = render_contacts(_settings(studio_phone=bad_phone, whatsapp_phone=bad_phone))
    labels = [label for label, _, _ in _buttons(markup)]
    assert "📞 Позвонить" not in labels
    assert "💬 Написать в WhatsApp" not in labels


def test_invalid_telegram_username_never_becomes_url():
    _, markup = render_contacts(_settings(telegram_username="x/../../evil?ref=1"))
    assert not any(label == "✈️ Написать в Telegram" for label, _, _ in _buttons(markup))


def test_appointment_detail_contacts_button_uses_tenant_callback_not_url():
    appointment = SimpleNamespace(id=42, status=AppointmentStatus.CONFIRMED)
    markup = get_appointment_detail_keyboard(appointment)
    contact_buttons = [button for row in markup.inline_keyboard for button in row if button.text == "📞 Контакты"]

    assert len(contact_buttons) == 1
    assert contact_buttons[0].url is None
    assert MenuCallback.unpack(contact_buttons[0].callback_data).action == "contact"


@pytest.mark.asyncio
async def test_call_button_sends_native_contact_card_for_valid_phone(monkeypatch):
    monkeypatch.setattr(app_settings, "app_mode", "polling")
    lookup = AsyncMock(return_value=_settings(studio_phone="+7 (999) 000-00-00"))
    monkeypatch.setattr(MasterSettingsRepository, "get_by_master_id", lookup)
    message = SimpleNamespace(answer_contact=AsyncMock())
    callback = SimpleNamespace(message=message, answer=AsyncMock())

    await cb_contact_call(callback, object(), 123)

    lookup.assert_awaited_once_with(123)
    message.answer_contact.assert_awaited_once_with(
        phone_number="+79990000000", first_name="Мастер"
    )
    callback.answer.assert_awaited_once_with()


@pytest.mark.asyncio
@pytest.mark.parametrize("phone", [None, "123", "javascript:alert(1)"])
async def test_call_button_rejects_missing_or_invalid_phone(monkeypatch, phone):
    monkeypatch.setattr(app_settings, "app_mode", "polling")
    lookup = AsyncMock(return_value=_settings(studio_phone=phone) if phone else None)
    monkeypatch.setattr(MasterSettingsRepository, "get_by_master_id", lookup)
    message = SimpleNamespace(answer_contact=AsyncMock())
    callback = SimpleNamespace(message=message, answer=AsyncMock())

    await cb_contact_call(callback, object(), 123)

    lookup.assert_awaited_once_with(123)
    message.answer_contact.assert_not_awaited()
    callback.answer.assert_awaited_once_with("Телефон мастера недоступен", show_alert=True)


@requires_postgres
@pytest.mark.asyncio
async def test_webhook_call_enqueues_one_tenant_bound_contact_without_direct_send(
    pg_session: AsyncSession, monkeypatch
):
    monkeypatch.setattr(app_settings, "app_mode", "webhook")
    owner = await _user(pg_session, "Contact owner")
    master = await _master(pg_session, owner, "Contact tenant")
    instance = BotInstance(
        master_id=master.id,
        telegram_bot_id=2 * 10**12 + (uuid4().int % 10**11),
        encrypted_token="opaque-test-ciphertext",
        status=BotInstanceStatus.ACTIVE,
        is_current=True,
        token_version=1,
    )
    pg_session.add(instance)
    await pg_session.flush()
    await MasterContactsService(pg_session).update_field(
        master.id, owner.id, "studio_phone", "+7 (999) 000-00-00"
    )
    scope = f"customer:{uuid4().hex}"
    pg_session.info.update(
        trusted_master_id=master.id,
        bot_instance_id=instance.id,
        webhook_update_scope=scope,
        webhook_update_id=987654,
    )
    message = SimpleNamespace(chat=SimpleNamespace(id=123456789), answer_contact=AsyncMock())
    callback = SimpleNamespace(message=message, answer=AsyncMock())

    await cb_contact_call(callback, pg_session, master.id)
    await cb_contact_call(callback, pg_session, master.id)

    key = f"contact-card:{scope}:987654"
    rows = (await pg_session.execute(
        select(TelegramOutbox).where(TelegramOutbox.idempotency_key == key)
    )).scalars().all()
    assert len(rows) == 1
    assert rows[0].master_id == master.id
    assert rows[0].bot_instance_id == instance.id
    assert rows[0].target_chat_id == 123456789
    assert rows[0].operation_type == "send_contact"
    assert rows[0].payload == {"phone_number": "+79990000000", "first_name": "Мастер"}
    message.answer_contact.assert_not_awaited()
    callback.answer.assert_not_awaited()


@pytest.mark.asyncio
async def test_outbox_worker_dispatches_send_contact():
    bot = SimpleNamespace(send_contact=AsyncMock())
    row = _Delivery(
        id=1,
        master_id=2,
        bot_instance_id=3,
        operation_type="send_contact",
        target_chat_id=123456789,
        payload={"phone_number": "+79990000000", "first_name": "Мастер"},
        attempts=1,
    )

    await _send(bot, row)

    bot.send_contact.assert_awaited_once_with(
        chat_id=123456789, phone_number="+79990000000", first_name="Мастер"
    )
