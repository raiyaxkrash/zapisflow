"""Regression tests for production Manager Bot UI hotfixes."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiogram.exceptions import TelegramBadRequest

from app.manager_bot.handlers import cb_help, cb_main_menu, cb_projects


@pytest.mark.asyncio
async def test_reopening_projects_ignores_unchanged_telegram_message() -> None:
    callback = SimpleNamespace(
        from_user=SimpleNamespace(id=123),
        message=SimpleNamespace(
            edit_text=AsyncMock(
                side_effect=TelegramBadRequest(
                    method=MagicMock(), message="Bad Request: message is not modified"
                )
            )
        ),
        answer=AsyncMock(),
    )
    state = SimpleNamespace(clear=AsyncMock())
    with patch("app.manager_bot.handlers._get_or_create_user", new=AsyncMock(return_value=SimpleNamespace(id=5))):
        with patch("app.manager_bot.handlers.MasterRepository") as repository:
            repository.return_value.list_by_owner_id = AsyncMock(return_value=[])
            await cb_projects(callback, state, AsyncMock())
    callback.answer.assert_awaited_once()
    callback.message.edit_text.assert_awaited_once()


@pytest.mark.asyncio
async def test_reopening_projects_propagates_other_telegram_errors() -> None:
    callback = SimpleNamespace(
        from_user=SimpleNamespace(id=123),
        message=SimpleNamespace(
            edit_text=AsyncMock(
                side_effect=TelegramBadRequest(method=MagicMock(), message="Bad Request: chat not found")
            )
        ),
        answer=AsyncMock(),
    )
    state = SimpleNamespace(clear=AsyncMock())
    with patch("app.manager_bot.handlers._get_or_create_user", new=AsyncMock(return_value=SimpleNamespace(id=5))):
        with patch("app.manager_bot.handlers.MasterRepository") as repository:
            repository.return_value.list_by_owner_id = AsyncMock(return_value=[])
            with pytest.raises(TelegramBadRequest, match="chat not found"):
                await cb_projects(callback, state, AsyncMock())


@pytest.mark.asyncio
async def test_reopening_main_menu_ignores_unchanged_message_and_escapes_name() -> None:
    callback = SimpleNamespace(
        from_user=SimpleNamespace(id=123),
        message=SimpleNamespace(
            edit_text=AsyncMock(
                side_effect=TelegramBadRequest(
                    method=MagicMock(), message="Bad Request: message is not modified"
                )
            )
        ),
        answer=AsyncMock(),
    )
    state = SimpleNamespace(clear=AsyncMock())
    with patch(
        "app.manager_bot.handlers._get_or_create_user",
        new=AsyncMock(return_value=SimpleNamespace(id=5, first_name="<test>")),
    ):
        with patch("app.manager_bot.handlers.PlatformAdminService") as admin_svc_cls:
            admin_svc_cls.return_value.is_platform_admin = AsyncMock(return_value=False)
            await cb_main_menu(callback, state, AsyncMock())
    callback.answer.assert_awaited_once()
    assert "&lt;test&gt;" in callback.message.edit_text.await_args.args[0]


@pytest.mark.asyncio
async def test_reopening_help_ignores_unchanged_message() -> None:
    callback = SimpleNamespace(
        message=SimpleNamespace(
            edit_text=AsyncMock(
                side_effect=TelegramBadRequest(
                    method=MagicMock(), message="Bad Request: message is not modified"
                )
            )
        ),
        answer=AsyncMock(),
    )
    await cb_help(callback)
    callback.answer.assert_awaited_once()
