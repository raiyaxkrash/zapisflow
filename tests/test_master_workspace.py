"""Master workspace adapters: live PostgreSQL, existing booking domain and RBAC."""
from datetime import datetime
import uuid
import pytest
import test_miniapp as base
from app.repositories.service_repository import ServiceRepository
system=base.system
miniapp_database_url=base.miniapp_database_url

@pytest.mark.asyncio
async def test_master_calendar_counts_and_client_summary(system):
    c=await base.login(system)
    hold=await base.make_hold(system)
    await base.post(c,'/client/appointments',{'appointment_id':hold['id'],'phone':'+79991234567','policy_agreed':True})
    c=await base.login(system,11001,raw=base.signed(11001,query_id=str(uuid.uuid4())))
    day=hold['start_time'][:10]
    result=await c.get('/api/miniapp/master/calendar',params={'year':int(day[:4]),'month':int(day[5:7])})
    assert result.status_code==200,result.text
    assert next(row for row in result.json()['days'] if row['date']==day)['appointment_count']==1
    rows=(await c.get('/api/miniapp/master/clients')).json()
    record=next(row for row in rows if row['total_bookings']==1)
    assert record['next_visit']
    details=(await c.get(f"/api/miniapp/master/appointments/{hold['id']}")).json()
    assert details['master_client_id']==record['id']

@pytest.mark.asyncio
async def test_queue_is_tenant_scoped_and_restricted_to_admin(system):
    async with system.factory() as s:
        await ServiceRepository(s).update_service(system.services[0].id,system.masters[0].id,deposit_value=50)
        await s.commit()
    await base.login(system)
    hold=await base.make_hold(system)
    c=await base.login(system,11001,raw=base.signed(11001,query_id=str(uuid.uuid4())))
    result=await c.get('/api/miniapp/master/payments')
    assert result.status_code==200,result.text
    assert result.json()[0]['id']==hold['id']
    c=await base.login(system,11002,bot_index=1)
    assert (await c.get('/api/miniapp/master/payments')).json()==[]
    c=await base.login(system,12001)
    assert (await c.get('/api/miniapp/master/payments')).status_code==403

@pytest.mark.asyncio
@pytest.mark.parametrize("action,expected",[("cancel","CANCELLED_BY_ADMIN"),("complete","COMPLETED")])
async def test_master_cancel_is_idempotent_and_cross_tenant_denied(system,action,expected):
    await base.login(system)
    hold=await base.make_hold(system)
    if action=='complete':
        await base.post(system.client,'/client/appointments',{'appointment_id':hold['id'],'phone':'+79991234567','policy_agreed':True})
    c=await base.login(system,11002,bot_index=1)
    assert (await base.post(c,f"/master/appointments/{hold['id']}/action",{'action':action})).status_code==404
    c=await base.login(system,12001)
    assert (await base.post(c,f"/master/appointments/{hold['id']}/action",{'action':action})).status_code==403
    c=await base.login(system,11001,raw=base.signed(11001,query_id=str(uuid.uuid4())))
    key=str(uuid.uuid4())
    first=await base.post(c,f"/master/appointments/{hold['id']}/action",{'action':action},key)
    assert first.status_code==200,first.text
    assert first.json()['status']==expected
    assert (await base.post(c,f"/master/appointments/{hold['id']}/action",{'action':action},key)).json()==first.json()

@pytest.mark.asyncio
async def test_master_free_windows_share_slot_engine_and_reject_other_tenant(system):
    c=await base.login(system)
    slot=await base.first_slot(system)
    c=await base.login(system,11001,raw=base.signed(11001,query_id=str(uuid.uuid4())))
    params={'target_date':slot[:10],'service_id':system.services[0].id,'staff_id':system.staffs[0].id}
    result=await c.get('/api/miniapp/master/free-windows',params=params)
    assert result.status_code==200,result.text
    assert slot in result.json()['slots']
    params['staff_id']=system.staffs[1].id
    assert (await c.get('/api/miniapp/master/free-windows',params=params)).status_code==404

@pytest.mark.asyncio
@pytest.mark.parametrize('path',['analytics','broadcasts','portfolio'])
async def test_master_management_reads_deny_staff_and_clients(system,path):
    c=await base.login(system)
    assert (await c.get('/api/miniapp/master/'+path)).status_code==403
    c=await base.login(system,12001)
    assert (await c.get('/api/miniapp/master/'+path)).status_code==403
    c=await base.login(system,11001,raw=base.signed(11001,query_id=str(uuid.uuid4())))
    r=await c.get('/api/miniapp/master/'+path)
    assert r.status_code==200,r.text

@pytest.mark.asyncio
async def test_portfolio_upload_reuses_storage_and_idempotency_and_tenant_guards(system):
    import io
    from types import SimpleNamespace
    from PIL import Image
    async def photo(*args,**kwargs):
        system.registry.bot.sends+=1
        return SimpleNamespace(photo=[SimpleNamespace(file_id='local-photo',file_unique_id='local-unique')])
    system.registry.bot.send_photo=photo
    out=io.BytesIO();Image.new('RGB',(5,5),'blue').save(out,format='PNG');raw=out.getvalue()
    c=await base.login(system,11001,raw=base.signed(11001,query_id=str(uuid.uuid4())))
    key=str(uuid.uuid4())
    async def send():return await c.post('/api/miniapp/master/portfolio',files={'file':('../unsafe.png',raw,'image/png')},headers={'Idempotency-Key':key})
    r=await send();assert r.status_code==200,r.text
    assert (await send()).json()==r.json()
    assert system.registry.bot.sends==1
    rows=(await c.get('/api/miniapp/master/portfolio')).json();assert rows[0]['id']==r.json()['id']
    c=await base.login(system,11002,bot_index=1)
    assert (await c.get(f"/api/miniapp/master/portfolio/{r.json()['id']}/image")).status_code==404
    assert (await base.post(c,f"/master/portfolio/{r.json()['id']}",{},method='DELETE')).status_code==404
    c=await base.login(system,11001,raw=base.signed(11001,query_id=str(uuid.uuid4())))
    bad=await c.post('/api/miniapp/master/portfolio',files={'file':('x.svg',b'<svg/>','image/svg+xml')},headers={'Idempotency-Key':str(uuid.uuid4())})
    assert bad.json()["code"]=="UPLOAD_INVALID"
    assert (await base.post(c,f"/master/portfolio/{r.json()['id']}",{},method='DELETE')).status_code==200
    assert (await c.get('/api/miniapp/master/portfolio')).json()==[]
