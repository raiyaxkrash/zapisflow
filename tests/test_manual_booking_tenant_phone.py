"""A typed phone number cannot attach another master's Telegram profile."""

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from app.database.models.master import MasterClient
from app.repositories.user_repository import UserRepository
from tests.conftest import requires_postgres
from tests.test_phase_9_subscription import _create_master, _create_user


@requires_postgres
@pytest.mark.asyncio
async def test_manual_phone_lookup_requires_current_master_client(pg_engine: AsyncEngine) -> None:
    sessions = async_sessionmaker(pg_engine, expire_on_commit=False)
    async with sessions() as session:
        owner_a = await _create_user(session, "Phone owner A")
        owner_b = await _create_user(session, "Phone owner B")
        client_a = await _create_user(session, "Phone client A")
        client_a.phone = "+79990000001"
        master_a = await _create_master(session, owner_a.id, "Phone master A")
        master_b = await _create_master(session, owner_b.id, "Phone master B")
        session.add(MasterClient(master_id=master_a.id, user_id=client_a.id))
        await session.commit()

        repository = UserRepository(session)
        assert await repository.get_by_phone_exact("+79990000001", master_a.id) == client_a
        assert await repository.get_by_phone_exact("+79990000001", master_b.id) is None
