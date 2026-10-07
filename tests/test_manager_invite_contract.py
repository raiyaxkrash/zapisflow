"""Real handler/repository regressions: Telegram identity and atomic staff invites."""

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import test_miniapp as base
from sqlalchemy import select

from app.database.models import MasterAdmin, StaffMember, User
from app.manager_bot.handlers import cmd_start
from app.repositories.staff_repository import StaffRepository

system = base.system
miniapp_database_url = base.miniapp_database_url


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["valid", "expired", "used", "wrong"])
async def test_manager_invite_resolves_internal_user_and_handles_lifecycle(
    system, mode, caplog
):
    async with system.factory() as session:
        repo = StaffRepository(session)
        staff = await repo.create_staff(system.masters[0].id, "Invited specialist")
        token = await repo.create_invite_token(staff.id, system.masters[0].id)
        if mode == "expired":
            staff.invite_expires_at = datetime.now(UTC) - timedelta(minutes=1)
        if mode == "used":
            staff.invite_used_at = datetime.now(UTC)
        if mode == "wrong":
            token = "wrong-token-not-an-invitation"
        await session.commit()
        staff_id = staff.id
    telegram_id = 9900012345
    message = SimpleNamespace(
        text="/start inv_" + token,
        from_user=SimpleNamespace(
            id=telegram_id, first_name="Invite client", last_name=None, username=None
        ),
        answer=AsyncMock(),
    )
    async with system.factory() as session:
        await cmd_start(message, AsyncMock(), session)
        await session.commit()
        user = await session.scalar(select(User).where(User.telegram_id == telegram_id))
        row = await session.get(StaffMember, staff_id)
        membership = await session.scalar(
            select(MasterAdmin).where(
                MasterAdmin.master_id == system.masters[0].id,
                MasterAdmin.user_id == user.id,
            )
        )
        assert user.id != telegram_id
        if mode == "valid":
            assert row.user_id == user.id and row.invite_token_hash is None
            assert membership is not None and membership.role.value == "STAFF"
            assert "Invited specialist" in message.answer.call_args.args[0]
        else:
            assert row.user_id is None and membership is None
            assert "недействительна" in message.answer.call_args.args[0]
    assert token not in caplog.text


@pytest.mark.asyncio
async def test_staff_invite_concurrency_and_tenant_scope(system):
    async with system.factory() as session:
        repo = StaffRepository(session)
        staff = await repo.create_staff(system.masters[0].id, "Concurrent staff")
        other = await repo.create_staff(system.masters[1].id, "Other business")
        assert await repo.create_invite_token(other.id, system.masters[0].id) is None
        token = await repo.create_invite_token(staff.id, system.masters[0].id)
        users = [
            User(telegram_id=9900012350 + i, first_name="Concurrent") for i in range(2)
        ]
        session.add_all(users)
        await session.commit()
        user_ids = [u.id for u in users]
        staff_id, other_id = staff.id, other.id

    async def claim(user_id):
        async with system.factory() as session:
            result = await StaffRepository(session).claim_invite_token_atomic(
                raw_token=token, user_id=user_id
            )
            await session.commit()
            return result.id if result else None

    results = await asyncio.gather(*(claim(uid) for uid in user_ids))
    assert results.count(staff_id) == 1 and results.count(None) == 1
    assert await claim(user_ids[1]) is None
    async with system.factory() as session:
        row = await session.get(StaffMember, staff_id)
        assert row.user_id in user_ids and row.master_id == system.masters[0].id
        assert (await session.get(StaffMember, other_id)).user_id is None
