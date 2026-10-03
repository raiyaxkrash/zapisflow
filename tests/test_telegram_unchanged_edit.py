"""An unchanged Telegram edit does not cause webhook replay or DB rollback."""

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from aiogram.exceptions import TelegramBadRequest
from aiogram.types import Update
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.bot.middlewares.db_session import DbSessionMiddleware
from app.database.models.processed_update import ProcessedWebhookUpdate
from app.database.models.setting import AppSetting
from tests.conftest import requires_postgres


@pytest.mark.asyncio
@pytest.mark.parametrize("message,commits", [
    ("Bad Request: message is not modified", True),
    ("Bad Request: query is too old and response timeout expired or query ID is invalid", True),
    ("Bad Request: message to edit not found", False),
])
async def test_unchanged_edit_is_only_telegram_error_committed(message: str, commits: bool) -> None:
    session = MagicMock()
    session.info = {}
    session.commit = AsyncMock()
    session.rollback = AsyncMock()
    session.close = AsyncMock()
    context = MagicMock()
    context.__aenter__ = AsyncMock(return_value=session)
    context.__aexit__ = AsyncMock(return_value=False)
    handler = AsyncMock(side_effect=TelegramBadRequest(method=MagicMock(), message=message))

    with patch("app.bot.middlewares.db_session.async_session_factory", return_value=context):
        if commits:
            await DbSessionMiddleware()(handler, object(), {})
            session.commit.assert_awaited_once()
            session.rollback.assert_not_awaited()
        else:
            with pytest.raises(TelegramBadRequest):
                await DbSessionMiddleware()(handler, object(), {})
            session.rollback.assert_awaited_once()
            session.commit.assert_not_awaited()


@requires_postgres
@pytest.mark.asyncio
async def test_expired_callback_ack_keeps_business_state_and_ledger(
    pg_session: AsyncSession, monkeypatch,
) -> None:
    import app.bot.middlewares.db_session as middleware_module

    monkeypatch.setattr(
        middleware_module,
        "async_session_factory",
        async_sessionmaker(pg_session.bind, expire_on_commit=False, join_transaction_mode="create_savepoint"),
    )
    update = Update(update_id=uuid4().int % 1_000_000_000)
    key = "expired_callback_" + uuid4().hex[:16]
    calls = 0

    async def handler(_event, data):
        nonlocal calls
        calls += 1
        data["session"].add(AppSetting(key=key, value={"updated": True}))
        raise TelegramBadRequest(
            method=MagicMock(),
            message="Bad Request: query is too old and response timeout expired or query ID is invalid",
        )

    middleware = DbSessionMiddleware()
    await middleware(handler, update, {"webhook_update_scope": "manager"})
    await middleware(handler, update, {"webhook_update_scope": "manager"})
    assert calls == 1
    assert (await pg_session.get(AppSetting, key)).value == {"updated": True}
    assert await pg_session.get(ProcessedWebhookUpdate, ("manager", update.update_id)) is not None
