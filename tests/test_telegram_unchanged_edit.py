"""An unchanged Telegram edit does not cause webhook replay or DB rollback."""

from unittest.mock import AsyncMock, MagicMock, patch

from aiogram.exceptions import TelegramBadRequest
import pytest

from app.bot.middlewares.db_session import DbSessionMiddleware


@pytest.mark.asyncio
@pytest.mark.parametrize("message,commits", [
    ("Bad Request: message is not modified", True),
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
