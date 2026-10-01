"""Transactional enqueue API for tenant Telegram notifications.

Callers use their existing business AsyncSession and must not commit here. The
middleware commits the business mutation, outbox row, and webhook marker once.
"""

from __future__ import annotations

from typing import Any

from aiogram.types import InlineKeyboardMarkup
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import TOKEN_REGEX, TELEGRAM_API_URL_REGEX
from app.database.models.telegram_outbox import TelegramOutbox, TelegramOutboxStatus


def bound_bot_instance_id(session: AsyncSession, master_id: int) -> int:
    """Return only the server-bound BotInstance for this session and master."""
    bound_master_id = session.info.get("trusted_master_id")
    bot_instance_id = session.info.get("bot_instance_id")
    if bound_master_id != master_id or not isinstance(bot_instance_id, int) or bot_instance_id <= 0:
        raise ValueError("Trusted BotInstance binding is required for Telegram outbox")
    return bot_instance_id


def _payload_is_safe(value: Any) -> bool:
    if isinstance(value, str):
        return TOKEN_REGEX.search(value) is None and TELEGRAM_API_URL_REGEX.search(value) is None
    if isinstance(value, dict):
        forbidden = {"token", "bot_token", "encrypted_token", "password", "secret", "encryption_key"}
        return all(str(k).lower() not in forbidden and _payload_is_safe(v) for k, v in value.items())
    if isinstance(value, list):
        return all(_payload_is_safe(item) for item in value)
    return True


def _keyboard_payload(reply_markup: InlineKeyboardMarkup | None) -> dict[str, Any] | None:
    if reply_markup is None:
        return None
    if not isinstance(reply_markup, InlineKeyboardMarkup):
        raise TypeError("Only InlineKeyboardMarkup is supported in the durable outbox")
    return reply_markup.model_dump(mode="json", exclude_none=True)


async def _enqueue(
    session: AsyncSession,
    *,
    master_id: int,
    bot_instance_id: int,
    chat_id: int,
    operation_type: str,
    payload: dict[str, Any],
    idempotency_key: str,
) -> TelegramOutbox:
    if master_id <= 0 or bot_instance_id <= 0 or chat_id == 0:
        raise ValueError("Valid tenant, bot instance, and target chat are required")
    if not idempotency_key or len(idempotency_key) > 255:
        raise ValueError("Outbox idempotency key must be 1-255 characters")
    if not _payload_is_safe(payload):
        raise ValueError("Outbox payload contains a credential or forbidden secret field")

    row_id = (
        await session.execute(
            pg_insert(TelegramOutbox)
            .values(
                master_id=master_id,
                bot_instance_id=bot_instance_id,
                operation_type=operation_type,
                target_chat_id=chat_id,
                payload=payload,
                idempotency_key=idempotency_key,
                status=TelegramOutboxStatus.PENDING,
            )
            .on_conflict_do_nothing(index_elements=[TelegramOutbox.idempotency_key])
            .returning(TelegramOutbox.id)
        )
    ).scalar_one_or_none()
    if row_id is not None:
        row = await session.get(TelegramOutbox, row_id)
    else:
        row = (
            await session.execute(
                select(TelegramOutbox).where(TelegramOutbox.idempotency_key == idempotency_key)
            )
        ).scalar_one()
        if (
            row.master_id != master_id
            or row.bot_instance_id != bot_instance_id
            or row.target_chat_id != chat_id
            or row.operation_type != operation_type
            or row.payload != payload
        ):
            raise ValueError("Outbox idempotency key collides with a different delivery")
    assert row is not None
    return row


async def enqueue_telegram_message(
    session: AsyncSession,
    *,
    master_id: int,
    bot_instance_id: int,
    chat_id: int,
    text: str,
    idempotency_key: str,
    reply_markup: InlineKeyboardMarkup | None = None,
) -> TelegramOutbox:
    """Queue a tenant text message in the caller's business transaction."""
    return await _enqueue(
        session,
        master_id=master_id,
        bot_instance_id=bot_instance_id,
        chat_id=chat_id,
        operation_type="send_message",
        payload={"text": text, "reply_markup": _keyboard_payload(reply_markup)},
        idempotency_key=idempotency_key,
    )


async def enqueue_telegram_contact(
    session: AsyncSession,
    *,
    master_id: int,
    bot_instance_id: int,
    chat_id: int,
    phone_number: str,
    first_name: str,
    idempotency_key: str,
) -> TelegramOutbox:
    """Queue a native contact card in the webhook transaction."""
    return await _enqueue(
        session,
        master_id=master_id,
        bot_instance_id=bot_instance_id,
        chat_id=chat_id,
        operation_type="send_contact",
        payload={"phone_number": phone_number, "first_name": first_name},
        idempotency_key=idempotency_key,
    )


async def enqueue_telegram_photo(
    session: AsyncSession,
    *,
    master_id: int,
    bot_instance_id: int,
    chat_id: int,
    photo_file_id: str,
    caption: str | None,
    idempotency_key: str,
    reply_markup: InlineKeyboardMarkup | None = None,
) -> TelegramOutbox:
    return await _enqueue(
        session,
        master_id=master_id,
        bot_instance_id=bot_instance_id,
        chat_id=chat_id,
        operation_type="send_photo",
        payload={
            "photo_file_id": photo_file_id,
            "caption": caption,
            "reply_markup": _keyboard_payload(reply_markup),
        },
        idempotency_key=idempotency_key,
    )


async def enqueue_telegram_document(
    session: AsyncSession,
    *,
    master_id: int,
    bot_instance_id: int,
    chat_id: int,
    document_file_id: str,
    caption: str | None,
    idempotency_key: str,
    reply_markup: InlineKeyboardMarkup | None = None,
) -> TelegramOutbox:
    return await _enqueue(
        session,
        master_id=master_id,
        bot_instance_id=bot_instance_id,
        chat_id=chat_id,
        operation_type="send_document",
        payload={
            "document_file_id": document_file_id,
            "caption": caption,
            "reply_markup": _keyboard_payload(reply_markup),
        },
        idempotency_key=idempotency_key,
    )


async def enqueue_telegram_edit(
    session: AsyncSession,
    *,
    master_id: int,
    bot_instance_id: int,
    chat_id: int,
    message_id: int,
    text: str,
    idempotency_key: str,
    reply_markup: InlineKeyboardMarkup | None = None,
) -> TelegramOutbox:
    """Queue an idempotent edit of an existing Telegram message."""
    if message_id <= 0:
        raise ValueError("A positive Telegram message_id is required for edits")
    return await _enqueue(
        session,
        master_id=master_id,
        bot_instance_id=bot_instance_id,
        chat_id=chat_id,
        operation_type="edit_message_text",
        payload={
            "message_id": message_id,
            "text": text,
            "reply_markup": _keyboard_payload(reply_markup),
        },
        idempotency_key=idempotency_key,
    )


async def enqueue_telegram_edit_caption(
    session: AsyncSession,
    *,
    master_id: int,
    bot_instance_id: int,
    chat_id: int,
    message_id: int,
    caption: str,
    idempotency_key: str,
    reply_markup: InlineKeyboardMarkup | None = None,
) -> TelegramOutbox:
    """Queue an idempotent caption edit for an existing media message."""
    if message_id <= 0:
        raise ValueError("A positive Telegram message_id is required for edits")
    return await _enqueue(
        session,
        master_id=master_id,
        bot_instance_id=bot_instance_id,
        chat_id=chat_id,
        operation_type="edit_message_caption",
        payload={
            "message_id": message_id,
            "caption": caption,
            "reply_markup": _keyboard_payload(reply_markup),
        },
        idempotency_key=idempotency_key,
    )
