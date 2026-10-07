import test from 'node:test';
import assert from 'node:assert/strict';
import { JSDOM } from 'jsdom';
import { readFile } from 'node:fs/promises';
import * as P from '../src/ui/primitives.js';
import * as B from '../src/ui/booking.js';
import { setupTheme } from '../src/theme/theme.js';
import { colorTokens } from '../src/theme/accent.js';
import { destinations,activeDestination,backDestination,createNavigation } from '../src/application/navigation.js';
import { createTelegramBridge } from '../src/telegram/bridge.js';
import { bootstrap } from '../src/telegram/bootstrap.js';

test('Canonical tokens remain identical across isolated Docker contexts',async()=>{
 const canonical=await readFile(new URL('../../shared/design/tokens.css',import.meta.url),'utf8');
 for(const path of ['../src/tokens.css','../../marketing/src/tokens.css'])assert.equal(await readFile(new URL(path,import.meta.url),'utf8'),canonical);
});
test('Primitives escape tenant labels, preserve native control semantics and link errors',()=>{
 const dom=new JSDOM(P.Button({label:'<img onerror=evil>',action:'nav',id:'" onfocus="evil'})+P.Input({label:'Phone',name:'phone',type:'tel',error:'Incorrect'})+P.Textarea({label:'Notes',name:'notes',value:'</textarea><script>x</script>'})+P.Switch({label:'Enabled',name:'enabled',checked:true})+P.SegmentedControl({label:'Mode',name:'mode',items:[['a','One'],['b','Two']],value:'a'}));
 const d=dom.window.document;assert.equal(d.querySelector('script'),null);assert.equal(d.querySelector('img'),null);assert.equal(d.querySelector('[onfocus]'),null);assert.equal(d.querySelector('input[type=tel]').getAttribute('aria-describedby'),'phone-error');assert.equal(d.querySelector('[role=switch]').checked,true);assert.equal(d.querySelector('input[type=radio]:checked').value,'a');dom.window.close();
});
test('Dialog / bottom sheet have modal naming and return focus to invoker',()=>{
 const dom=new JSDOM('<button id="open">Open</button>'+P.BottomSheet({id:'dialog',title:'Confirm',content:P.Button({label:'Continue'})}));const d=dom.window.document,dialog=d.querySelector('dialog'),invoker=d.querySelector('#open');let opened=false;dialog.showModal=()=>opened=true;assert.ok(P.openDialog(dialog,invoker));assert.ok(opened);assert.equal(dialog.getAttribute('aria-labelledby'),'dialog-title');dialog.dispatchEvent(new dom.window.Event('close'));assert.equal(d.activeElement,invoker);dom.window.close();
});
test('Navigation isolates roles, matches nested destinations and discards stale completions',()=>{
 assert.equal(destinations('master',{}).length,0);assert.equal(destinations('master',{can_manage:true}).length,2);assert.equal(destinations('master',{can_manage:true,can_edit_project:true}).length,4);assert.equal(activeDestination('confirm'),'services');assert.equal(backDestination('time','client',false),'services');const n=createNavigation(),old=n.begin('home');const current=n.begin('services',1);assert.equal(n.current(old),false);assert.equal(n.current(current),true);const retry=n.retry();retry[0]='evil';assert.equal(n.retry()[0],'services');
});
test('Browser System updates live, invalid preference fails safe and cleanup removes listeners',()=>{
 const dom=new JSDOM('<html></html>',{url:'https://app.test'});let change,removed=false;const media={matches:false,addEventListener(n,fn){change=fn;},removeEventListener(){removed=true;}};dom.window.matchMedia=()=>media;const theme=setupTheme(null,dom.window);assert.equal(theme.get(),'system');media.matches=true;change();assert.equal(dom.window.document.documentElement.dataset.theme,'dark');theme.set('invalid');assert.equal(theme.get(),'system');theme.set('light');change();assert.equal(dom.window.document.documentElement.dataset.theme,'light');theme.dispose();assert.ok(removed);dom.window.close();
});
test('Telegram themeParams are bounded, update without reload and manual preference wins',()=>{
 const dom=new JSDOM('<html></html>',{url:'https://app.test'});let change;const tg={colorScheme:'dark',themeParams:{header_bg_color:'#101626',text_color:'#ffffff'},onEvent(n,fn){change=fn;}};const theme=setupTheme(tg,dom.window);assert.equal(dom.window.document.documentElement.style.getPropertyValue('--zf-native-header-bg'),'#101626');theme.set('light');assert.equal(dom.window.document.documentElement.style.getPropertyValue('--zf-native-header-bg'),'');tg.colorScheme='dark';change();assert.equal(dom.window.document.documentElement.dataset.theme,'light');theme.set('system');tg.themeParams={header_bg_color:'#FFFFFF',text_color:'#FFFFFF'};change();assert.equal(dom.window.document.documentElement.style.getPropertyValue('--zf-native-header-bg'),'');dom.window.close();
});
test('Accent hover/active remain distinct and preserve foreground contrast at boundary colors',()=>{
 for(const color of ['#FFFFCC','#FF0000','#000080','#FFFFFF','#00FF00','#AA00FF','#1D72FE']){
 const tokens=colorTokens(color);const luminance=hex=>{const c=hex.slice(1).match(/../g).map(v=>parseInt(v,16)/255).map(v=>v<=.04045?v/12.92:((v+.055)/1.055)**2.4);return c[0]*.2126+c[1]*.7152+c[2]*.0722;};for(const state of ['--zf-accent','--zf-accent-hover','--zf-accent-active']){const l=luminance(tokens[state]);assert.ok(tokens['--zf-accent-foreground']==='#000000'?(l+.05)/.05>=4.5:1.05/(l+.05)>=4.5);}}
 const blue=colorTokens('#1D72FE');assert.notEqual(blue['--zf-accent'],blue['--zf-accent-hover']);assert.notEqual(blue['--zf-accent-hover'],blue['--zf-accent-active']);
});
test('Telegram helper version gates, replaces callbacks and prevents duplicate primary actions',()=>{
 let visible=0,handlers=new Set(),haptics=0;const tg={isVersionAtLeast:()=>true,MainButton:{onClick(fn){handlers.add(fn)},offClick(fn){handlers.delete(fn)},setText(){},show(){visible++},hide(){},enable(){}},HapticFeedback:{notificationOccurred(){haptics++}}};const bridge=createTelegramBridge(tg,{});bridge.setPrimaryAction({label:'Continue',onClick(){}});bridge.setPrimaryAction({label:'Next',onClick(){}});assert.equal(handlers.size,1);bridge.setPrimaryAction(null);assert.equal(handlers.size,0);bridge.notify();assert.equal(haptics,1);tg.isVersionAtLeast=()=>false;bridge.notify();assert.equal(haptics,1);assert.equal(visible,2);
});
test('Viewport tracks keyboard and safe areas without a Telegram auth bypass',()=>{
 const dom=new JSDOM('<html></html>');let resized;dom.window.visualViewport={height:400,addEventListener(n,fn){resized=fn;}};dom.window.Telegram={WebApp:{initData:'fixture',viewportHeight:700,safeAreaInset:{top:30,bottom:20},contentSafeAreaInset:{top:40,bottom:10},ready(){},expand(){},onEvent(){}}};bootstrap(dom.window);const root=dom.window.document.documentElement;assert.equal(root.style.getPropertyValue('--app-safe-top'),'40px');assert.equal(root.dataset.keyboard,'open');dom.window.visualViewport.height=dom.window.innerHeight;resized();assert.equal(root.dataset.keyboard,'closed');dom.window.close();
});
test('Booking foundations consume server DTOs and escape markup; disabled slots remain disabled',()=>{
 const dom=new JSDOM(B.ServiceCard({id:1,title:'<script>x</script>',price:'1500',duration_min:60})+B.StaffCard({id:2,display_name:'Anna'})+B.SlotPicker([{label:'10:00',value:'server-slot',available:false}])+B.Calendar({year:2026,month:10,days:[],today:'2026-10-07'},{loading:true}));const d=dom.window.document;assert.equal(d.querySelector('script'),null);assert.equal(d.querySelector('[data-action=slot]').disabled,true);assert.ok(d.querySelector('[aria-busy=true]'));dom.window.close();
});
