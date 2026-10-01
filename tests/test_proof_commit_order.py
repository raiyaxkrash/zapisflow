"""Receipt notifications must describe a proof already committed to the database."""

from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.bot.handlers.client import payment as payment_handler
from app.database.models.payment import MediaType

TRUSTED_MASTER_ID = 42


def _context(monkeypatch, *, media_type=MediaType.PHOTO):
    events = []
    appointment = SimpleNamespace(
        id=17,
        start_time=datetime(2030, 10, 1, 10, tzinfo=UTC),
        snapshot_deposit_amount=Decimal("500"),
        snapshot_service_price=Decimal("1000"),
        snapshot_service_title="Маникюр",
    )
    payment = SimpleNamespace(id=29)

    async def submit(**kwargs):
        events.append("submit")
        return appointment, payment, SimpleNamespace(id=31)

    monkeypatch.setattr(
        payment_handler,
        "PaymentService",
        lambda session: SimpleNamespace(submit_payment_proof=submit),
    )
    monkeypatch.setattr(
        payment_handler,
        "MasterSettingsRepository",
        lambda session: SimpleNamespace(get_value=AsyncMock(return_value="UTC")),
    )
    monkeypatch.setattr(
        payment_handler,
        "MasterAuthorizationService",
        lambda session: SimpleNamespace(
            get_admin_recipients=AsyncMock(return_value=[1001])
        ),
    )
    monkeypatch.setattr(
        payment_handler,
        "settings",
        SimpleNamespace(admin_ids=[1001], timezone="UTC"),
    )

    def record(name):
        async def _record(*args, **kwargs):
            events.append(name)

        return _record

    media = SimpleNamespace(file_id="receipt-file", file_unique_id="receipt-unique")
    message = SimpleNamespace(
        photo=[media] if media_type == MediaType.PHOTO else None,
        document=media if media_type == MediaType.DOCUMENT else None,
        caption="Комментарий",
        answer=AsyncMock(side_effect=record("client_answer")),
    )
    state = SimpleNamespace(
        get_data=AsyncMock(return_value={"appointment_id": 17}),
        clear=AsyncMock(side_effect=record("clear_state")),
    )
    user = SimpleNamespace(
        id=5,
        first_name="Ирина",
        last_name=None,
        phone=None,
        username=None,
        telegram_id=5001,
    )
    bot = SimpleNamespace(
        send_photo=AsyncMock(side_effect=record("admin_photo")),
        send_document=AsyncMock(side_effect=record("admin_document")),
    )
    session = SimpleNamespace(
        commit=AsyncMock(side_effect=record("commit")), rollback=AsyncMock()
    )
    return events, message, state, user, bot, session


@pytest.mark.parametrize(
    ("media_type", "send_event"),
    [(MediaType.PHOTO, "admin_photo"), (MediaType.DOCUMENT, "admin_document")],
)
async def test_receipt_commits_before_client_and_admin_notifications(
    monkeypatch, media_type, send_event
):
    events, message, state, user, bot, session = _context(
        monkeypatch, media_type=media_type
    )

    await payment_handler.msg_receive_proof(message, state, user, bot, session, master_id=TRUSTED_MASTER_ID)

    assert events == ["submit", "commit", "clear_state", "client_answer", send_event]
    assert session.commit.await_count == 1
    session.rollback.assert_not_awaited()
    assert bot.send_photo.await_count + bot.send_document.await_count == 1


async def test_failed_commit_sends_no_success_or_admin_notification(monkeypatch):
    events, message, state, user, bot, session = _context(monkeypatch)
    session.commit.side_effect = RuntimeError("database commit failed")

    with pytest.raises(RuntimeError, match="database commit failed"):
        await payment_handler.msg_receive_proof(message, state, user, bot, session, master_id=TRUSTED_MASTER_ID)

    assert events == ["submit"]
    state.clear.assert_not_awaited()
    message.answer.assert_not_awaited()
    bot.send_photo.assert_not_awaited()
    bot.send_document.assert_not_awaited()


async def test_telegram_failure_after_commit_does_not_prevent_admin_notification(
    monkeypatch, caplog
):
    events, message, state, user, bot, session = _context(monkeypatch)

    async def failed_client_answer(*args, **kwargs):
        events.append("client_answer")
        raise RuntimeError("Telegram unavailable")

    message.answer.side_effect = failed_client_answer

    await payment_handler.msg_receive_proof(message, state, user, bot, session, master_id=TRUSTED_MASTER_ID)

    assert events == ["submit", "commit", "clear_state", "client_answer", "admin_photo"]
    assert session.commit.await_count == 1
    assert "Could not acknowledge committed proof" in caplog.text
    session.rollback.assert_not_awaited()


async def test_failed_admin_delivery_is_logged_without_rolling_back(monkeypatch, caplog):
    events, message, state, user, bot, session = _context(monkeypatch)

    async def failed_admin_send(**kwargs):
        events.append("admin_photo")
        raise RuntimeError("chat is blocked")

    bot.send_photo.side_effect = failed_admin_send
    message.caption = "<b>не доверять</b>"

    await payment_handler.msg_receive_proof(message, state, user, bot, session, master_id=TRUSTED_MASTER_ID)

    assert events == ["submit", "commit", "clear_state", "client_answer", "admin_photo"]
    assert "Could not notify admin" in caplog.text
    assert "&lt;b&gt;не доверять&lt;/b&gt;" in bot.send_photo.await_args.kwargs["caption"]
    session.rollback.assert_not_awaited()
