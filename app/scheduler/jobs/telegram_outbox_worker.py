"""Multi-replica worker for durable tenant Telegram notifications.

The database makes each business effect/outbox row durable and workers avoid
concurrent sends. Telegram delivery itself is at-least-once: a process crash
after Telegram accepts a message but before SENT commits can lead to a retry.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import logging
from typing import Awaitable, Callable
import uuid

from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter
from aiogram.types import InlineKeyboardMarkup
from sqlalchemy import and_, or_, select, update
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.config.settings import settings
from app.database.models.master import BotInstance, BotInstanceStatus
from app.database.models.telegram_outbox import TelegramOutbox, TelegramOutboxStatus
from app.database.session import async_session_maker
from app.services.bot_registry import BotRegistry
from app.utils.delivery_lock import OUTBOX_LOCK_NAMESPACE, delivery_is_in_flight, delivery_lock

logger = logging.getLogger(__name__)


def _retry_delay(attempts: int, retry_after: int | None = None) -> int:
    return min(3600, max(int(retry_after or 0), 5 * (2 ** min(attempts, 9))))


@dataclass(frozen=True)
class _Delivery:
    id: int
    master_id: int
    bot_instance_id: int
    operation_type: str
    target_chat_id: int
    payload: dict
    attempts: int


def _lock_item_id(row_id: int) -> int:
    # Two-key PostgreSQL advisory locks accept signed 32-bit item IDs. A rare
    # collision only serializes two deliveries; it cannot cause duplicates.
    return row_id % 2_147_483_647


async def _send(bot, row: _Delivery) -> None:
    payload = row.payload
    markup_data = payload.get("reply_markup")
    markup = InlineKeyboardMarkup.model_validate(markup_data) if markup_data else None
    if row.operation_type == "send_message":
        await bot.send_message(chat_id=row.target_chat_id, text=payload["text"], reply_markup=markup)
    elif row.operation_type == "send_contact":
        await bot.send_contact(
            chat_id=row.target_chat_id,
            phone_number=payload["phone_number"],
            first_name=payload["first_name"],
        )
    elif row.operation_type == "send_photo":
        await bot.send_photo(
            chat_id=row.target_chat_id,
            photo=payload["photo_file_id"],
            caption=payload.get("caption"),
            reply_markup=markup,
        )
    elif row.operation_type == "send_document":
        await bot.send_document(
            chat_id=row.target_chat_id,
            document=payload["document_file_id"],
            caption=payload.get("caption"),
            reply_markup=markup,
        )
    elif row.operation_type == "edit_message_text":
        await bot.edit_message_text(
            chat_id=row.target_chat_id,
            message_id=payload["message_id"],
            text=payload["text"],
            reply_markup=markup,
        )
    elif row.operation_type == "edit_message_caption":
        await bot.edit_message_caption(
            chat_id=row.target_chat_id,
            message_id=payload["message_id"],
            caption=payload["caption"],
            reply_markup=markup,
        )
    else:
        raise ValueError("Unsupported Telegram outbox operation")


async def _claim(
    session_maker: async_sessionmaker,
    *,
    owner: str,
    batch_size: int,
    max_attempts: int,
    lease_seconds: int,
) -> list[tuple[int, int]]:
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(seconds=lease_seconds)
    async with session_maker() as session:
        async with session.begin():
            rows = (
                await session.execute(
                    select(TelegramOutbox)
                    .where(
                        or_(
                            and_(
                                TelegramOutbox.status == TelegramOutboxStatus.PENDING,
                                or_(
                                    TelegramOutbox.next_attempt_at.is_(None),
                                    TelegramOutbox.next_attempt_at <= now,
                                ),
                            ),
                            and_(
                                TelegramOutbox.status == TelegramOutboxStatus.PROCESSING,
                                TelegramOutbox.claimed_at <= cutoff,
                            ),
                        )
                    )
                    .order_by(TelegramOutbox.created_at, TelegramOutbox.id)
                    .limit(batch_size)
                    .with_for_update(skip_locked=True)
                )
            ).scalars().all()
            claimed: list[tuple[int, int]] = []
            for row in rows:
                if row.status == TelegramOutboxStatus.PROCESSING and await delivery_is_in_flight(
                    await session.connection(), OUTBOX_LOCK_NAMESPACE, _lock_item_id(row.id)
                ):
                    continue
                if row.attempts >= max_attempts:
                    row.status = TelegramOutboxStatus.FAILED
                    row.claim_owner = None
                    row.claimed_at = None
                    row.last_error = "Maximum delivery attempts reached"
                    continue
                row.status = TelegramOutboxStatus.PROCESSING
                row.attempts += 1
                row.claim_owner = owner
                row.claimed_at = now
                row.next_attempt_at = None
                claimed.append((row.id, row.attempts))
            return claimed


async def _deliver_one(
    session_maker: async_sessionmaker,
    registry: BotRegistry,
    *,
    row_id: int,
    owner: str,
    attempt: int,
    max_attempts: int,
    after_send: Callable[[int], Awaitable[None]] | None = None,
) -> bool:
    engine = session_maker.kw["bind"]
    async with delivery_lock(engine, OUTBOX_LOCK_NAMESPACE, _lock_item_id(row_id)) as acquired:
        if not acquired:
            return False

        # Resolve the row and current bot in a short read transaction. The
        # session closes before any Telegram API call, leaving no open PG
        # transaction during the external side effect.
        try:
            async with session_maker() as session:
                row = await session.get(TelegramOutbox, row_id)
                if (
                    row is None
                    or row.status != TelegramOutboxStatus.PROCESSING
                    or row.claim_owner != owner
                    or row.attempts != attempt
                ):
                    return False
                delivery = _Delivery(
                    id=row.id,
                    master_id=row.master_id,
                    bot_instance_id=row.bot_instance_id,
                    operation_type=row.operation_type,
                    target_chat_id=row.target_chat_id,
                    payload=dict(row.payload),
                    attempts=row.attempts,
                )
                instance = await session.get(BotInstance, row.bot_instance_id)
                if (
                    instance is None
                    or instance.master_id != row.master_id
                    or not instance.is_current
                    or instance.status not in (BotInstanceStatus.ACTIVE, BotInstanceStatus.SETUP_REQUIRED)
                ):
                    raise RuntimeError("BotInstance unavailable or tenant binding changed")
                bot = await registry.get_by_instance_id(
                    row.bot_instance_id,
                    session=session,
                    expected_token_version=instance.token_version,
                )
        except Exception as exc:
            await _record_failure(session_maker, row_id, owner, attempt, max_attempts, exc)
            return False

        logger.info(
            "Telegram outbox dispatch outbox_id=%s master_id=%s bot_instance_id=%s attempt=%s operation_type=%s",
            delivery.id, delivery.master_id, delivery.bot_instance_id, delivery.attempts, delivery.operation_type,
        )
        try:
            try:
                await _send(bot, delivery)
            except TelegramBadRequest as exc:
                # Repeating an edit is safe. Telegram reports this on a
                # previous successful edit whose SENT write was lost.
                if delivery.operation_type not in {"edit_message_text", "edit_message_caption"} or "message is not modified" not in str(exc).lower():
                    raise
            if after_send is not None:
                await after_send(delivery.id)
        except Exception as exc:
            await _record_failure(session_maker, row_id, owner, attempt, max_attempts, exc)
            return False

        return await _record_sent(session_maker, delivery, owner, attempt)


async def _record_sent(
    session_maker: async_sessionmaker,
    delivery: _Delivery,
    owner: str,
    attempt: int,
) -> bool:
    async with session_maker() as session:
        async with session.begin():
            result = await session.execute(
                update(TelegramOutbox)
                .where(
                    TelegramOutbox.id == delivery.id,
                    TelegramOutbox.status == TelegramOutboxStatus.PROCESSING,
                    TelegramOutbox.claim_owner == owner,
                    TelegramOutbox.attempts == attempt,
                )
                .values(
                    status=TelegramOutboxStatus.SENT,
                    sent_at=datetime.now(timezone.utc),
                    claim_owner=None,
                    claimed_at=None,
                    next_attempt_at=None,
                    last_error=None,
                )
            )
            changed = result.rowcount == 1
    if changed:
        logger.info(
            "Telegram outbox sent outbox_id=%s master_id=%s bot_instance_id=%s attempt=%s operation_type=%s",
            delivery.id, delivery.master_id, delivery.bot_instance_id, delivery.attempts, delivery.operation_type,
        )
    return changed


async def _record_failure(
    session_maker: async_sessionmaker,
    row_id: int,
    owner: str,
    attempt: int,
    max_attempts: int,
    exc: Exception,
) -> None:
    permanent = isinstance(exc, (TelegramForbiddenError, TelegramBadRequest, ValueError))
    status = TelegramOutboxStatus.FAILED if permanent or attempt >= max_attempts else TelegramOutboxStatus.PENDING
    retry_after = exc.retry_after if isinstance(exc, TelegramRetryAfter) else None
    next_attempt = (
        datetime.now(timezone.utc) + timedelta(seconds=_retry_delay(attempt, retry_after))
        if status == TelegramOutboxStatus.PENDING else None
    )
    async with session_maker() as session:
        async with session.begin():
            result = await session.execute(
                update(TelegramOutbox)
                .where(
                    TelegramOutbox.id == row_id,
                    TelegramOutbox.status == TelegramOutboxStatus.PROCESSING,
                    TelegramOutbox.claim_owner == owner,
                    TelegramOutbox.attempts == attempt,
                )
                .values(
                    status=status,
                    next_attempt_at=next_attempt,
                    claim_owner=None,
                    claimed_at=None,
                    last_error=type(exc).__name__,
                )
            )
            changed = result.rowcount == 1
    if changed:
        logger.warning(
            "Telegram outbox retry state=%s outbox_id=%s attempt=%s",
            status.value, row_id, attempt,
        )


async def dispatch_telegram_outbox(
    *,
    registry: BotRegistry | None,
    session_maker: async_sessionmaker = async_session_maker,
    batch_size: int = 100,
    max_attempts: int = 5,
    lease_seconds: int | None = None,
    after_send: Callable[[int], Awaitable[None]] | None = None,
) -> int:
    """Claim due rows, send individually, and return the number marked SENT.

    `after_send` is an optional failure-injection hook used by crash-window
    tests; it is never configured in production.
    """
    if registry is None:
        logger.error("Telegram outbox dispatch requires BotRegistry")
        return 0
    if batch_size < 1 or max_attempts < 1:
        raise ValueError("batch_size and max_attempts must be positive")
    actual_lease = lease_seconds or settings.job_processing_timeout_seconds
    if actual_lease < 1:
        raise ValueError("lease_seconds must be positive")
    owner = uuid.uuid4().hex
    claimed = await _claim(
        session_maker,
        owner=owner,
        batch_size=batch_size,
        max_attempts=max_attempts,
        lease_seconds=actual_lease,
    )
    sent = 0
    for row_id, attempt in claimed:
        if await _deliver_one(
            session_maker,
            registry,
            row_id=row_id,
            owner=owner,
            attempt=attempt,
            max_attempts=max_attempts,
            after_send=after_send,
        ):
            sent += 1
    return sent
