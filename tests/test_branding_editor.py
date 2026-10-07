import io,json,uuid
import pytest
import test_miniapp as base
from PIL import Image
from app.services.branding import validate_brand_filename
system=base.system
miniapp_database_url=base.miniapp_database_url

def body(name='Studio Anna'):
 return {'branding':{'brand_name':name,'accent_color':'#C1354E','theme_mode':'light'},'contacts':{'studio_phone':'+79991234567','vk_profile':'https://vk.com/studio'},'delete_logo':False,'delete_cover':False}

def png():
 out=io.BytesIO();Image.new('RGB',(10,10),'red').save(out,format='PNG');return out.getvalue()

async def owner(system):return await base.login(system,11001,raw=base.signed(11001,query_id=str(uuid.uuid4())))

@pytest.mark.parametrize('filename',['../logo.png','x.svg','x.pdf','x','x\\logo.png'])
def test_brand_filename_rejects_paths_and_extensions(filename):
 with pytest.raises(ValueError):validate_brand_filename(filename,'image/png')

@pytest.mark.asyncio
async def test_atomic_editor_save_contacts_assets_and_retry(system):
 c=await owner(system);key=str(uuid.uuid4())
 async def send():return await c.post('/api/miniapp/master/branding/save',data={'payload':json.dumps(body())},files={'logo':('logo.png',png(),'image/png'),'cover':('cover.png',png(),'image/png')},headers={'Idempotency-Key':key})
 r=await send();assert r.status_code==200,r.text
 assert (await send()).json()==r.json()
 ctx=(await c.get('/api/miniapp/context')).json()
 assert ctx['project']['name']=='Studio Anna' and ctx['contacts']['studio_phone']=='+79991234567'
 assert ctx['branding']['logo_url'] and ctx['branding']['cover_url']
 request=body('Barber House');request['delete_logo']=True
 r=await c.post('/api/miniapp/master/branding/save',data={'payload':json.dumps(request)},headers={'Idempotency-Key':str(uuid.uuid4())})
 assert r.status_code==200,r.text
 assert r.json()['logo_url'] is None and r.json()['cover_url']

@pytest.mark.asyncio
async def test_invalid_second_asset_does_not_publish_first_asset_or_text(system):
 c=await owner(system);before=(await c.get('/api/miniapp/context')).json()
 r=await c.post('/api/miniapp/master/branding/save',data={'payload':json.dumps(body())},files={'logo':('logo.png',png(),'image/png'),'cover':('cover.png',b'not-an-image','image/png')},headers={'Idempotency-Key':str(uuid.uuid4())})
 assert r.status_code==413 and r.json()['code']=='UPLOAD_INVALID'
 after=(await c.get('/api/miniapp/context')).json()
 assert after['branding']==before['branding'] and after['contacts']==before['contacts']

@pytest.mark.asyncio
@pytest.mark.parametrize('user',[10001,12001])
async def test_new_editor_save_is_owner_only(system,user):
 c=await base.login(system,user)
 r=await c.post('/api/miniapp/master/branding/save',data={'payload':json.dumps(body())},headers={'Idempotency-Key':str(uuid.uuid4())})
 assert r.status_code==403

@pytest.mark.asyncio
async def test_editor_contact_injection_and_other_bot_header_rejected(system):
 c=await owner(system);request=body();request['contacts']['vk_profile']='javascript:alert(1)'
 r=await c.post('/api/miniapp/master/branding/save',data={'payload':json.dumps(request)},headers={'Idempotency-Key':str(uuid.uuid4())})
 assert r.status_code==422
 r=await c.post('/api/miniapp/master/branding/save',data={'payload':json.dumps(body())},headers={'Idempotency-Key':str(uuid.uuid4()),'X-MiniApp-Bot':str(system.bots[1].public_id)})
 assert r.status_code==401
