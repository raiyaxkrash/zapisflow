"""Receipt business state and notification intent share middleware transaction."""

from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.bot.handlers.client import payment as payment_handler
from app.database.models.payment import MediaType


def _context(monkeypatch, media_type):
    events = []
    appointment = SimpleNamespace(
        id=17,
        start_time=datetime(2030, 10, 1, 10, tzinfo=UTC),
        snapshot_deposit_amount=Decimal("500"),
        snapshot_service_price=Decimal("1000"),
        snapshot_service_title="Маникюр",
    )
    payment = SimpleNamespace(id=29)
    proof = SimpleNamespace(id=31)

    async def submit(**kwargs):
        events.append("submit")
        return appointment, payment, proof

    monkeypatch.setattr(
        payment_handler, "PaymentService",
        lambda session: SimpleNamespace(submit_payment_proof=submit),
    )
    monkeypatch.setattr(
        payment_handler, "MasterSettingsRepository",
        lambda session: SimpleNamespace(get_value=AsyncMock(return_value="UTC")),
    )
    monkeypatch.setattr(
        payment_handler, "MasterAuthorizationService",
        lambda session: SimpleNamespace(get_admin_recipients=AsyncMock(return_value=[1001])),
    )
    monkeypatch.setattr(payment_handler, "bound_bot_instance_id", lambda session, master_id: 77)

    async def queued(kind, **kwargs):
        events.append(kind)
        return SimpleNamespace(id=1)

    monkeypatch.setattr(
        payment_handler, "enqueue_telegram_message",
        lambda session, **kwargs: queued("client", **kwargs),
    )
    monkeypatch.setattr(
        payment_handler, "enqueue_telegram_photo",
        lambda session, **kwargs: queued("admin_photo", **kwargs),
    )
    monkeypatch.setattr(
        payment_handler, "enqueue_telegram_document",
        lambda session, **kwargs: queued("admin_document", **kwargs),
    )

    media = SimpleNamespace(file_id="receipt-file", file_unique_id="receipt-unique")
    message = SimpleNamespace(
        photo=[media] if media_type == MediaType.PHOTO else None,
        document=media if media_type == MediaType.DOCUMENT else None,
        caption="<b>не доверять</b>",
        answer=AsyncMock(),
    )
    state = SimpleNamespace(
        get_data=AsyncMock(return_value={"appointment_id": 17}), clear=AsyncMock()
    )
    user = SimpleNamespace(
        id=5, first_name="Ирина", last_name=None, phone=None,
        username=None, telegram_id=5001,
    )
    bot = SimpleNamespace(send_photo=AsyncMock(), send_document=AsyncMock())
    session = SimpleNamespace(info={}, commit=AsyncMock())
    return events, message, state, user, bot, session


@pytest.mark.parametrize(
    ("media_type", "admin_event"),
    [(MediaType.PHOTO, "admin_photo"), (MediaType.DOCUMENT, "admin_document")],
)
async def test_proof_queues_client_and_admin_in_handler_transaction(
    monkeypatch, media_type, admin_event
):
    events, message, state, user, bot, session = _context(monkeypatch, media_type)

    await payment_handler.msg_receive_proof(
        message, state, user, bot, session, master_id=42
    )

    assert events == ["submit", "client", admin_event]
    assert len(session.info["post_commit"]) == 1
    state.clear.assert_not_awaited()
    session.commit.assert_not_awaited()
    message.answer.assert_not_awaited()
    bot.send_photo.assert_not_awaited()
    bot.send_document.assert_not_awaited()


async def test_proof_enqueue_failure_does_not_commit_or_send(monkeypatch):
    events, message, state, user, bot, session = _context(monkeypatch, MediaType.PHOTO)

    async def fail(session, **kwargs):
        raise RuntimeError("outbox failed")

    monkeypatch.setattr(payment_handler, "enqueue_telegram_photo", fail)
    with pytest.raises(RuntimeError, match="outbox failed"):
        await payment_handler.msg_receive_proof(
            message, state, user, bot, session, master_id=42
        )

    assert events == ["submit", "client"]
    session.commit.assert_not_awaited()
    state.clear.assert_not_awaited()
    message.answer.assert_not_awaited()
    bot.send_photo.assert_not_awaited()
