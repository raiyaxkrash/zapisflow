import test from 'node:test';
import assert from 'node:assert/strict';
import {JSDOM} from 'jsdom';
import {Api} from '../src/api/client.js';
import {calendarKeys, scrollToPreview} from '../src/ui/behaviors.js';
for (const format of ['json','image']) test(`deadline includes stalled ${format} body`,async()=>{
 const api=new Api(async(_url,options)=>({ok:true,[format==='image'?'blob':'json']:()=>new Promise((_resolve,reject)=>options.signal.addEventListener('abort',()=>reject(Error('aborted'))))}),10);
 await assert.rejects(api.request('/slow',{},format),e=>e.code==='NETWORK');
});
test('calendar Home and End skip disabled dates',()=>{
 const dom=new JSDOM('<main><button class="calendar-day" disabled>1</button><button class="calendar-day">2</button><button class="calendar-day">3</button><button class="calendar-day" disabled>4</button></main>');
 const root=dom.window.document.querySelector('main'),days=root.querySelectorAll('button');
 for(const [key,expected] of [['Home',days[1]],['End',days[2]]]){calendarKeys({target:days[1],key,preventDefault(){}},root);assert.equal(dom.window.document.activeElement,expected);}
});
test('preview respects reduced motion',()=>{
 for(const reduced of [true,false]){let options;scrollToPreview({scrollIntoView(v){options=v}},{matchMedia:()=>({matches:reduced})});assert.equal(options.behavior,reduced?'auto':'smooth');}
});
