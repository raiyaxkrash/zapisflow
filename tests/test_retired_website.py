"""Retired website routes cannot create bookings or authenticate a browser."""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
import test_miniapp as base

from app.config.settings import Settings
from app.manager_bot.handlers import cb_retired_website_channel
from app.web.app import create_app

system = base.system
miniapp_database_url = base.miniapp_database_url

@pytest.mark.asyncio
@pytest.mark.parametrize("method,path", [
    ("GET", "/api/auth/telegram/login"),
    ("GET", "/api/auth/telegram/callback"),
    ("GET", "/api/auth/me"),
    ("POST", "/api/auth/logout"),
    ("GET", "/api/web-booking/account/bookings"),
    ("GET", "/api/web-booking/11111111-2222-3333-4444-555555555555/context"),
    ("POST", "/api/web-booking/11111111-2222-3333-4444-555555555555/holds"),
    ("POST", "/api/web-booking/11111111-2222-3333-4444-555555555555/confirm"),
])
async def test_retired_api_routes_are_not_registered(method, path):
    app = create_app()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=base.ORIGIN) as client:
        response = await client.request(method, path)
    assert response.status_code == 404
    assert "set-cookie" not in response.headers

@pytest.mark.asyncio
async def test_deprecated_website_flag_does_not_gate_miniapp(system):
    async with system.factory() as session:
        bot = await session.get(base.BotInstance, system.bots[0].id)
        bot.web_booking_enabled = True
        await session.commit()
    client = await base.login(system, user=system.owners[0].telegram_id)
    response = await client.get("/api/miniapp/context")
    assert response.status_code == 200
    assert response.json()["capabilities"]["can_manage"]

@pytest.mark.asyncio
async def test_old_manager_button_only_answers_without_mutation():
    callback = SimpleNamespace(answer=AsyncMock())
    await cb_retired_website_channel(callback)
    callback.answer.assert_awaited_once()
    assert callback.answer.call_args.kwargs["show_alert"] is True

def test_website_auth_configuration_is_retired():
    assert not any(name.startswith(("telegram_login_", "web_session_", "web_booking_")) for name in Settings.model_fields)
