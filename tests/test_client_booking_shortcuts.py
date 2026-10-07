from decimal import Decimal
import pytest
import test_miniapp as base
from sqlalchemy import select, func
from app.database.models import Appointment, Service
from app.database.models.service import DepositType

system = base.system
miniapp_database_url = base.miniapp_database_url

@pytest.mark.asyncio
async def test_quote_is_authoritative_and_creates_no_appointment(system):
    async with system.factory() as session:
        service = await session.get(Service, system.services[0].id)
        service.deposit_type = DepositType.PERCENT
        service.deposit_value = Decimal('30')
        await session.commit()
    client = await base.login(system)
    result = await client.get(f'/api/miniapp/client/quote?service_id={system.services[0].id}')
    assert result.status_code == 200, result.text
    assert result.json()['price'] == '499.00'
    assert Decimal(result.json()['deposit']) == Decimal('149.70')
    assert result.json()['duration_min'] == 60
    async with system.factory() as session:
        assert await session.scalar(select(func.count()).select_from(Appointment)) == 0

@pytest.mark.asyncio
async def test_nearest_uses_the_same_slots_and_tenant_staff(system):
    client = await base.login(system)
    result = await client.get(f'/api/miniapp/client/availability/nearest?service_id={system.services[0].id}')
    assert result.status_code == 200, result.text
    data = result.json()
    assert data['staff_id'] == system.staffs[0].id
    slots = await client.get(f"/api/miniapp/client/slots?service_id={system.services[0].id}&staff_id={data['staff_id']}&target_date={data['slot'][:10]}")
    assert data['slot'] in slots.json()['slots']

@pytest.mark.asyncio
@pytest.mark.parametrize('endpoint',['quote','availability/nearest'])
async def test_shortcuts_reject_cross_tenant_service_and_staff(system, endpoint):
    client = await base.login(system)
    assert (await client.get(f'/api/miniapp/client/{endpoint}?service_id={system.services[1].id}')).status_code == 404
    assert (await client.get(f'/api/miniapp/client/{endpoint}?service_id={system.services[0].id}&staff_id={system.staffs[1].id}')).status_code == 404
