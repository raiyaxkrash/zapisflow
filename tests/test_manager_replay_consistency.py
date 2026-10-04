"""PostgreSQL replay checks for Manager Bot business mutations."""

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from aiogram.types import Update
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.bot.middlewares.db_session import DbSessionMiddleware
from app.core.token_crypto import TokenCrypto
from app.database.models.master import BotInstance, Master
from app.database.models.processed_update import ProcessedWebhookUpdate
from app.database.models.user import User
from app.manager_bot.handlers import msg_new_master_name
from app.services.bot_provisioning_service import BotProvisioningService
from app.services.telegram_provisioning_gateway import BotIdentity
from tests.conftest import requires_postgres


@requires_postgres
@pytest.mark.asyncio
async def test_project_creation_and_processed_marker_commit_together(pg_session: AsyncSession, monkeypatch) -> None:
    """A crash before middleware commit leaves neither a project nor a marker."""
    import app.bot.middlewares.db_session as db_middleware

    session_factory = async_sessionmaker(
        bind=pg_session.bind,
        expire_on_commit=False,
        join_transaction_mode="create_savepoint",
    )
    monkeypatch.setattr(db_middleware, "async_session_factory", session_factory)
    middleware = DbSessionMiddleware()
    unique_id = 8_800_000 + uuid4().int % 1_000_000_000
    project_name = f"Replay-safe studio {unique_id}"
    update = Update(update_id=unique_id)
    message = MagicMock()
    message.text = project_name
    message.from_user = MagicMock(
        id=unique_id,
        first_name="Owner",
        last_name="",
        username="owner",
    )
    message.answer = AsyncMock()
    state = MagicMock(clear=AsyncMock())

    async def handler(_event, data):
        await msg_new_master_name(message, state, data["session"])

    async def crash_before_marker(event, data):
        await handler(event, data)
        raise RuntimeError("injected crash before processed marker")

    data = {"webhook_update_scope": "manager"}
    with pytest.raises(RuntimeError, match="injected crash"):
        await middleware(crash_before_marker, update, data.copy())

    assert await pg_session.scalar(select(func.count(Master.id)).where(Master.display_name == project_name)) == 0
    assert await pg_session.get(ProcessedWebhookUpdate, ("manager", update.update_id)) is None
    state.clear.assert_not_awaited()

    await middleware(handler, update, data.copy())
    await middleware(handler, update, data.copy())
    state.clear.assert_awaited_once()
    assert await pg_session.scalar(select(func.count(Master.id)).where(Master.display_name == project_name)) == 1
    user = await pg_session.scalar(select(User).where(User.telegram_id == unique_id))
    assert user is not None and user.trial_claimed_at is not None
    assert await pg_session.get(ProcessedWebhookUpdate, ("manager", update.update_id)) is not None


class _Gateway:
    def __init__(self) -> None:
        self.set_calls = 0
        self.delete_calls = 0
        self.url = ""

    async def set_chat_menu_button(self, token, menu_button):
        self.menu_button = menu_button
        return True

    async def set_webhook(self, token, url, secret_token, **kwargs):
        self.set_calls += 1
        self.url = url
        return True

    async def get_webhook_info(self, token):
        return MagicMock(url=self.url)

    async def validate_token(self, token):
        return BotIdentity(id=8_800_002, username="replay_bot", first_name="Replay")

    async def delete_webhook(self, token):
        self.delete_calls += 1
        return True


@requires_postgres
@pytest.mark.asyncio
async def test_connect_and_rotate_replay_keep_one_instance_and_version(pg_session: AsyncSession) -> None:
    owner = User(telegram_id=8_800_002, first_name="Owner")
    pg_session.add(owner)
    await pg_session.flush()
    master = Master(owner_user_id=owner.id, display_name="Connect replay")
    pg_session.add(master)
    await pg_session.commit()

    gateway = _Gateway()
    crypto = TokenCrypto("0123456789abcdef" * 4)
    service = BotProvisioningService(pg_session, gateway=gateway, crypto=crypto)
    identity = BotIdentity(id=8_800_002, username="replay_bot", first_name="Replay")
    original_token = "8800002:ORIGINAL_TOKEN"

    first = await service.provision_bot(master.id, owner.id, original_token, identity)
    replayed = await service.provision_bot(master.id, owner.id, original_token, identity)
    assert replayed.id == first.id
    assert gateway.set_calls == 1
    assert await pg_session.scalar(select(func.count(BotInstance.id)).where(BotInstance.master_id == master.id, BotInstance.is_current.is_(True))) == 1

    new_token = "8800002:ROTATED_TOKEN"
    await service.rotate_token(first.id, owner.id, new_token)
    await service.rotate_token(first.id, owner.id, new_token)
    await pg_session.refresh(first)
    assert first.token_version == 2
    assert gateway.set_calls == 2

    await service.disable_bot(first.id, owner.id)
    await service.disable_bot(first.id, owner.id)
    assert gateway.delete_calls == 1

    await service.enable_bot(first.id, owner.id)
    await service.enable_bot(first.id, owner.id)
    assert gateway.set_calls == 3
