import test from 'node:test';
import assert from 'node:assert/strict';
import { JSDOM } from 'jsdom';
import { bootstrap, botIdFromPath } from '../src/telegram/bootstrap.js';
import { setupTheme } from '../src/theme/theme.js';
import { Api } from '../src/api/client.js';
import { canManage, escape, bookingCard } from '../src/ui.js';

test('Telegram bootstrap uses raw initData, ready, viewport and safe-area events',()=>{
  const dom=new JSDOM('<html></html>',{url:'https://app.test'});let ready=0;const events={};
  dom.window.Telegram={WebApp:{initData:'signed-data',initDataUnsafe:{user:{id:999}},ready(){ready++},expand(){},viewportHeight:640,safeAreaInset:{top:12},contentSafeAreaInset:{top:20},onEvent(n,f){events[n]=f}}};
  const tg=bootstrap(dom.window);assert.equal(tg.initData,'signed-data');assert.equal(ready,1);
  assert.equal(dom.window.document.documentElement.style.getPropertyValue('--app-safe-top'),'20px');
  assert.ok(events.viewportChanged);assert.ok(events.contentSafeAreaChanged);
});
test('Outside Telegram fails closed',()=>{assert.throws(()=>bootstrap({}),/Откройте/)});
test('Opaque bot context requires UUID',()=>{
  assert.equal(botIdFromPath('/b/11111111-2222-3333-4444-555555555555'),'11111111-2222-3333-4444-555555555555');
  assert.throws(()=>botIdFromPath('/b/17'));
});
test('Manual theme preference persists and overrides Telegram',()=>{
  const dom=new JSDOM('<html></html>',{url:'https://app.test'});const events={};
  const tg={colorScheme:'dark',onEvent(n,f){events[n]=f}};
  const theme=setupTheme(tg,dom.window);assert.equal(dom.window.document.documentElement.dataset.theme,'dark');
  theme.set('light');assert.equal(dom.window.document.documentElement.dataset.theme,'light');tg.colorScheme='dark';events.themeChanged();
  assert.equal(dom.window.document.documentElement.dataset.theme,'light');assert.equal(dom.window.localStorage.getItem('zapisflow-theme'),'light');
  theme.set('system');assert.equal(dom.window.document.documentElement.dataset.theme,'dark');
});
test('Client cannot see management capability',()=>{
  assert.equal(canManage({capabilities:{can_manage:false}}),false);assert.equal(canManage({capabilities:{can_manage:true}}),true);
});
test('Stored text is escaped in appointment cards',()=>{
  const markup=bookingCard({service:'<script>bad</script>',status_label:'<b>status</b>',staff:'A&B',start_time:'2026-10-04T10:00:00+03:00',price:'499',payment:[]});
  assert.ok(!markup.includes('<script>'));assert.ok(markup.includes('&lt;script&gt;'));assert.equal(escape('"'), '&quot;');
});
test('Authentication sends original initData only',async()=>{
  let request;const api=new Api(async(url,options)=>{request={url,options};return {ok:true,json:async()=>({csrf_token:'csrf'})}});
  await api.auth('public-id','raw-signed-data');assert.deepEqual(JSON.parse(request.options.body),{bot_public_id:'public-id',init_data:'raw-signed-data'});
  assert.equal(api.csrf,'csrf');assert.equal(request.options.credentials,'same-origin');
});
test('Network retry uses same idempotency key, no auth in storage',async()=>{
  const requests=[];const api=new Api(async(url,options)=>{requests.push(options);if(requests.length===1)throw Error('offline');return {ok:true,json:async()=>({id:7})}});api.csrf='csrf';
  await assert.rejects(api.mutate('/client/holds',{service_id:1}),/Нет соединения/);
  assert.deepEqual(await api.mutate('/client/holds',{service_id:1}),{id:7});
  assert.equal(requests[0].headers['Idempotency-Key'],requests[1].headers['Idempotency-Key']);
});
test('Slot conflict and session expiry preserve safe error codes',async()=>{
  for(const [code,status] of [['SLOT_TAKEN',409],['SESSION_EXPIRED',401]]){
    const api=new Api(async()=>({ok:false,status,json:async()=>({code,message:'Safe error'})}));
    await assert.rejects(api.get('/client/slots'),error=>error.code===code && error.status===status);
  }
});
