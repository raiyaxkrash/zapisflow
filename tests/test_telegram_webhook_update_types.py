from unittest.mock import AsyncMock, patch

import pytest
from aiogram import Bot

from app.services.telegram_provisioning_gateway import (
    REQUIRED_WEBHOOK_UPDATES,
    TelegramProvisioningGateway,
)


@pytest.mark.asyncio
async def test_set_webhook_explicitly_subscribes_to_client_callbacks_and_messages():
    gateway = TelegramProvisioningGateway()
    bot = AsyncMock(spec=Bot)
    bot.session = AsyncMock()
    bot.set_webhook.return_value = True

    with patch.object(gateway, "_create_temp_bot", return_value=bot):
        assert await gateway.set_webhook("token", "https://example.test/webhook")

    assert bot.set_webhook.await_args.kwargs["allowed_updates"] == [
        "message",
        "callback_query",
    ]
    assert list(REQUIRED_WEBHOOK_UPDATES) == ["message", "callback_query"]
    bot.session.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_set_webhook_preserves_extra_update_types_and_adds_required_types():
    gateway = TelegramProvisioningGateway()
    bot = AsyncMock(spec=Bot)
    bot.session = AsyncMock()
    bot.set_webhook.return_value = True

    with patch.object(gateway, "_create_temp_bot", return_value=bot):
        await gateway.set_webhook(
            "token",
            "https://example.test/webhook",
            allowed_updates=["edited_message", "callback_query"],
        )

    assert bot.set_webhook.await_args.kwargs["allowed_updates"] == [
        "edited_message",
        "callback_query",
        "message",
    ]
    bot.session.close.assert_awaited_once()
