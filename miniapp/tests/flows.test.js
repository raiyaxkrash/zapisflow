import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { JSDOM } from 'jsdom';
import * as ui from '../src/ui.js';

const source = (await readFile(new URL('../src/app.js', import.meta.url),'utf8')).replace(/^import .*;\r?\n/gm,'');
const visit = {id:7,service:'Haircut',staff:'Master',price:'499',deposit:'0',payment:[],start_time:'2026-10-05T10:00:00+03:00',status:'WAITING_PAYMENT',status_label:'Резерв',cancel_allowed:true,policy_agreed:false};
const offering = {id:1,title:'Haircut',price:'499',duration_min:60,buffer_min:15,deposit_type:'FIXED',deposit_value:'0',is_active:true};
const flush = () => new Promise(resolve=>setTimeout(resolve,10));

async function harness({owner=false,networkFailures=0,deposit='0',authFailure=false,authDelay=0}={}) {
  const dom = new JSDOM('<html><div id="app"></div></html>',{url:'https://app.test/b/11111111-2222-3333-4444-555555555555',runScripts:'outside-only'});
  const calls = []; let current = {...visit,deposit}, failures = networkFailures;
  const ctx = {project:{name:'Real studio',timezone:'Europe/Moscow'},user:{first_name:'Client',phone:'+79991234567'},contacts:{studio_address:'<script>bad</script>'},today:'2026-10-05',booking_horizon_days:14,cancel_policy_hours:24,can_book:true,capabilities:{can_manage:owner,can_edit_project:owner}};
  class Api {
    async auth(...args){calls.push(['auth',...args]);if(authDelay)await new Promise(resolve=>setTimeout(resolve,authDelay));if(authFailure){const error=Error('Вход недоступен');error.status=401;throw error}return {csrf_token:'csrf'}}
    async get(path){
      calls.push(['get',path]);
      if(path==='/context')return ctx;
      if(path.includes('/services'))return path.endsWith('/1')?offering:[offering];
      if(path.includes('/staff'))return [{id:2,display_name:'Master',is_active:true,service_ids:[1]}];
      if(path.includes('/slots'))return {slots:[visit.start_time]};
      if(path.endsWith('/payment'))return {appointment:current,requisites:{bank_name:'Bank',bank_card_number:'Test',bank_recipient_name:'Master'}};
      if(path.includes('/appointments/'))return {...current,client:'Client'};
      if(path.includes('/appointments'))return [current];
      if(path.includes('/clients/'))return {id:3,name:'Client',notes:'',total_bookings:1,total_spent:'499',history:[current]};
      if(path.includes('/clients'))return [{id:3,name:'Client'}];
      if(path.includes('/schedule'))return {weekly:[],dates:[]};
      if(path.includes('/settings'))return {};
      throw Error('Unexpected read: '+path);
    }
    async mutate(path,body,method){
      calls.push(['mutate',path,body,method]);
      if(path==='/client/holds' && failures-->0){const error=Error('Offline');error.code='NETWORK';throw error}
      if(path==='/client/appointments')current={...current,policy_agreed:true,status:Number(deposit)?'WAITING_PAYMENT':'CONFIRMED',payment:Number(deposit)?[{id:9,status:'PENDING',amount:deposit,proofs:[]}]:[]};
      if(path.endsWith('/cancel'))current={...current,status:'CANCELLED_BY_CLIENT',cancel_allowed:false};
      return current;
    }
    async upload(path,file,progress){calls.push(['upload',path,file.type]);progress(100);current={...current,status:'PAYMENT_PROOF_SENT',payment:[{id:9,status:'SUBMITTED',amount:deposit,proofs:[{id:11}]}]};return {appointment:current}}
  }
  Object.assign(dom.window,{Api,...ui,e:ui.escape,b:ui.button,f:ui.field,
    bootstrap:()=>({initData:'signed',BackButton:{hide(){},show(){},onClick(){}}}),
    setupTheme:()=>({get:()=> 'system',set(){}}),botIdFromPath:()=> '11111111-2222-3333-4444-555555555555'});
  dom.window.confirm=()=>true;
  dom.window.eval(source+'\nwindow.testApp={route};');
  await flush();
  const click=async(action,id)=>{const button=dom.window.document.querySelector(`[data-action="${action}"]${id===undefined?'':`[data-id="${id}"]`}`);assert.ok(button,`${action}:${id} exists`);button.click();await flush()};
  const submit=async(id)=>{const form=dom.window.document.getElementById(id);assert.ok(form,id);form.dispatchEvent(new dom.window.Event('submit',{bubbles:true,cancelable:true}));await flush()};
  return {dom,calls,click,submit,root:dom.window.document.getElementById('app')};
}

test('Client UI drives service/staff/slot/hold/confirmation/my bookings/cancel through API',async()=>{
  const h=await harness();assert.ok(!h.root.querySelector('[data-action="mode"]'));
  await h.click('choose-service',1);await h.click('staff','any');await h.click('slot',visit.start_time);
  assert.ok(h.root.querySelector('#confirm-form'));h.dom.window.document.querySelector('[name=policy]').checked=true;
  await h.submit('confirm-form');assert.ok(h.root.textContent.includes('Мои записи'));
  assert.equal(h.calls.filter(c=>c[0]==='mutate'&&c[1]==='/client/holds').length,1);
  assert.ok(h.calls.find(c=>c[1]==='/client/appointments')[2].policy_agreed);
  await h.click('cancel',7);assert.ok(h.calls.find(c=>c[1]==='/client/appointments/7/cancel'));
  h.dom.window.close();
});

test('Lost hold response retries the same action, including repeated network failures',async()=>{
  const h=await harness({networkFailures:2});await h.click('choose-service',1);await h.click('staff','any');await h.click('slot',visit.start_time);
  await h.click('retry');await h.click('retry');assert.ok(h.root.querySelector('#confirm-form'));
  assert.equal(h.calls.filter(c=>c[1]==='/client/holds').length,3);h.dom.window.close();
});

test('Unconfirmed hold can resume from My appointments',async()=>{
  const h=await harness();await h.click('nav','bookings');await h.click('resume',7);
  assert.ok(h.root.querySelector('#confirm-form'));h.dom.window.close();
});

test('Manual prepayment shows requisites and uploads proof with progress',async()=>{
  const h=await harness({deposit:'50'});await h.click('choose-service',1);await h.click('staff','any');await h.click('slot',visit.start_time);
  h.dom.window.document.querySelector('[name=policy]').checked=true;await h.submit('confirm-form');
  assert.ok(h.root.textContent.includes('Bank'));
  const file=new h.dom.window.File(['image'],'proof.png',{type:'image/png'});
  Object.defineProperty(h.dom.window.document.getElementById('proof-file'),'files',{value:[file]});
  await h.submit('proof-form');assert.ok(h.calls.find(c=>c[0]==='upload'));h.dom.window.close();
});

test('Owner UI edits service, CRM notes and irregular schedule through tenant API',async()=>{
  const h=await harness({owner:true});await h.click('mode','master');
  await h.dom.window.testApp.route('service-edit',1);await h.submit('service-form');
  assert.ok(h.calls.find(c=>c[1]==='/master/services/1'&&c[3]==='PUT'));
  await h.dom.window.testApp.route('client',3);h.dom.window.document.querySelector('[name=notes]').value='Tenant notes';await h.submit('notes-form');
  assert.equal(h.calls.find(c=>c[1]==='/master/clients/3'&&c[0]==='mutate')[2].notes,'Tenant notes');
  await h.dom.window.testApp.route('schedule',2);await h.submit('schedule-form');
  assert.ok(h.calls.find(c=>c[1]==='/master/schedule'&&c[0]==='mutate')[2].target_date);
  h.dom.window.close();
});

test('Contact text is escaped by the real page renderer',async()=>{
  const h=await harness();await h.click('nav','contact');assert.ok(!h.root.querySelector('script'));assert.ok(h.root.textContent.includes('<script>bad</script>'));h.dom.window.close();
});

test('Auth loading transitions to authenticated client home',async()=>{
  const h=await harness({authDelay:50});assert.ok(h.root.textContent.includes('Проверяем вход'));
  await new Promise(resolve=>setTimeout(resolve,70));assert.ok(h.root.textContent.includes('Real studio'));h.dom.window.close();
});

test('Auth failure never exposes client or master data',async()=>{
  const h=await harness({owner:true,authFailure:true});assert.ok(h.root.textContent.includes('откройте его снова'));
  assert.ok(!h.root.querySelector('[data-action="mode"]'));assert.ok(!h.calls.some(c=>c[0]==='get'));h.dom.window.close();
});

test('Owner manual booking submits only scoped identifiers and optional phone',async()=>{
  const h=await harness({owner:true});await h.click('mode','master');await h.click('nav','manual');
  h.dom.window.document.querySelector('[name=phone]').value='+79991234567';await h.submit('manual-form');
  const request=h.calls.find(c=>c[0]==='mutate'&&c[1]==='/master/appointments');assert.ok(request);
  assert.equal(request[2].phone,'+79991234567');assert.equal(request[2].staff_id,2);
  assert.ok(!('master_id' in request[2])&&!('price' in request[2]));h.dom.window.close();
});
