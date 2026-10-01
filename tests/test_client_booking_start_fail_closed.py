from unittest.mock import AsyncMock

import pytest

from app.bot.handlers.client.booking import cb_start_booking


@pytest.mark.asyncio
async def test_booking_start_answers_when_trusted_tenant_context_is_missing():
    callback = AsyncMock()

    await cb_start_booking(
        callback=callback,
        state=AsyncMock(),
        session=AsyncMock(),
        master_id=None,
    )

    callback.answer.assert_awaited_once_with(
        "Не удалось определить проект. Пожалуйста, откройте бота заново.",
        show_alert=True,
    )
