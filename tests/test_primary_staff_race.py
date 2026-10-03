"""The first staff member is provisioned once across independent DB sessions."""

import asyncio
import uuid

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from app.database.models.master import Master
from app.database.models.staff import StaffMember
from app.database.models.user import User
from app.repositories.staff_repository import StaffRepository
from tests.conftest import requires_postgres


@requires_postgres
@pytest.mark.asyncio
async def test_100_concurrent_primary_staff_requests_create_one(pg_engine: AsyncEngine) -> None:
    sessions = async_sessionmaker(pg_engine, expire_on_commit=False)
    async with sessions.begin() as session:
        owner = User(telegram_id=9_000_000_000 + uuid.uuid4().int % 1_000_000_000,
                     first_name="Race Owner")
        session.add(owner)
        await session.flush()
        master = Master(owner_user_id=owner.id, display_name="Race Studio")
        session.add(master)
        await session.flush()
        master_id, owner_id = master.id, owner.id

    async def get_primary() -> int:
        async with sessions.begin() as session:
            staff = await StaffRepository(session).get_primary_or_default(master_id)
            assert staff is not None
            return staff.id

    try:
        ids = await asyncio.gather(*(get_primary() for _ in range(100)))
        assert len(set(ids)) == 1
        async with sessions() as session:
            count = await session.scalar(select(func.count(StaffMember.id)).where(
                StaffMember.master_id == master_id
            ))
            assert count == 1
    finally:
        async with sessions.begin() as session:
            await session.execute(delete(StaffMember).where(StaffMember.master_id == master_id))
            await session.execute(delete(Master).where(Master.id == master_id))
            await session.execute(delete(User).where(User.id == owner_id))
