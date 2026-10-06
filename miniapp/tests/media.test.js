import test from 'node:test';import assert from 'node:assert/strict';import {JSDOM} from 'jsdom';import {bindPortfolioMedia} from '../src/media.js';
test('Private media becomes a revocable blob URL and failures stay human-readable',async()=>{
 const dom=new JSDOM('<main><figure><img data-private-image="/client/portfolio/1/image"></figure></main>');const urls=[];dom.window.URL.createObjectURL=()=> 'blob:local-portfolio';dom.window.URL.revokeObjectURL=u=>urls.push(u);
 const root=dom.window.document.querySelector('main');const stop=bindPortfolioMedia(root,{image:async()=>new Blob(['image'],{type:'image/jpeg'})});await new Promise(r=>setTimeout(r,0));assert.equal(root.querySelector('img').src,'blob:local-portfolio');stop();assert.deepEqual(urls,['blob:local-portfolio']);
 root.querySelector('img').removeAttribute('src');const stopFailure=bindPortfolioMedia(root,{image:async()=>{throw Error('secret internal error')}});await new Promise(r=>setTimeout(r,0));assert.match(root.textContent,/Не удалось загрузить/);assert.ok(!root.textContent.includes('secret'));stopFailure();dom.window.close();
});
