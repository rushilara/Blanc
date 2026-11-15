import fs from 'node:fs';
import assert from 'node:assert/strict';
const tab = await (await fetch(`${process.env.CDP_URL || 'http://127.0.0.1:9233'}/json/new?about:blank`, { method: 'PUT' })).json();
const ws = new WebSocket(tab.webSocketDebuggerUrl);
await new Promise(resolve => ws.addEventListener('open', resolve));
let next = 0;
const pending = new Map();
ws.addEventListener('message', event => {
  const message = JSON.parse(event.data);
  if (message.id) { pending.get(message.id)?.(message); pending.delete(message.id); }
});
const send = (method, params = {}) => new Promise((resolve, reject) => {
  const id = ++next;
  pending.set(id, message => message.error ? reject(message.error) : resolve(message.result));
  ws.send(JSON.stringify({ id, method, params }));
});
const evaluate = async expression => {
  const result = await send('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true });
  if (result.exceptionDetails) throw new Error(result.exceptionDetails.text);
  return result.result.value;
};
const wait = async expression => {
  for (let n = 0; n < 100; n++) {
    if (await evaluate(expression)) return;
    await new Promise(resolve => setTimeout(resolve, 100));
  }
  throw new Error(`Timed out: ${expression}`);
};


// Run against an isolated Next dev preview. Only synthetic HTTP fixtures are used.
const base=JSON.parse(fs.readFileSync(new URL('../../fixtures/synthetic_analysis_revision.json',import.meta.url)));
const child=JSON.parse(fs.readFileSync(new URL('../../fixtures/synthetic_analysis_revision_excluded.json',import.meta.url)));
const records=JSON.parse(fs.readFileSync(new URL('../../fixtures/synthetic_records.json',import.meta.url)));
base.timeline_events=[]; child.timeline_events=[];
const sibling={...child,analysis_id:'sibling',needs_review:true};
const other={...base,analysis_id:'other-investigation',question:'An unrelated investigation?'};
const revisions=[child,sibling,other,base];
const openDrawer=async()=>{await evaluate(`[...document.querySelectorAll('button')].find(b=>b.textContent==='Saved versions').focus();[...document.querySelectorAll('button')].find(b=>b.textContent==='Saved versions').click()`);await wait(`!!document.querySelector('dialog[open] [data-revision-id]')`);};
const openVersion=async id=>evaluate(`document.querySelector('dialog[open] [data-revision-id="${id}"] button').click()`);
const hasActive=id=>`!![...document.querySelectorAll('main p')].find(p=>p.textContent.includes('Revision ${id}'))`;
try {
await send('Page.enable');
await send('Emulation.setFocusEmulationEnabled',{enabled:true});
await send('Emulation.setDeviceMetricsOverride',{width:1440,height:1100,deviceScaleFactor:1,mobile:false});
await send('Page.addScriptToEvaluateOnNewDocument',{source:`
const revisions=${JSON.stringify(revisions)}, records=${JSON.stringify(records)};
window.calls=[];window.failHistory=false;window.failRevision=false;window.delayRevision=false;window.delaySource=false;window.missingSource=false;
window.fetch=async(url,init)=>{
const path=new URL(url,location.href).pathname;window.calls.push({path,method:init?.method||'GET'});
if(path==='/health')return Response.json({status:'ok',config:{}});
if(path==='/cases')return Response.json([{case_id:'synthetic-demo',title:'Synthetic case A',import_status:'ready'},{case_id:'case-b',title:'Empty case B',import_status:'empty'}]);
if(path.endsWith('/analyses')){
 if(window.failHistory)return new Response('',{status:500});
 return Response.json(path.includes('/case-b/')?[]:revisions.map(r=>({analysis_id:r.analysis_id,parent_analysis_id:r.parent_analysis_id,question:r.question,needs_review:!!r.needs_review,created_at:r.created_at,excluded_record_ids:r.excluded_record_ids,restored_from_analysis_id:null})));
}
if(path.includes('/analyses/')){
 if(window.delayRevision)await new Promise(r=>setTimeout(r,1000));
 if(window.failRevision)return new Response('',{status:503});
 return Response.json(revisions.find(r=>r.analysis_id===decodeURIComponent(path.split('/').pop())));
}
if(path.includes('/records/')){
 if(window.delaySource)await new Promise(r=>setTimeout(r,1000));
 const id=decodeURIComponent(path.split('/').pop());
 if(window.missingSource&&id==='rec-02')return new Response('',{status:404});
 return Response.json({record:records.find(r=>r.record_id===id),excerpt:null});
}
throw new Error('Unexpected request '+path);
};`});
await send('Page.navigate',{url:process.env.PREVIEW_URL||'http://127.0.0.1:3006/live'});
await wait(`document.querySelector('#live-case')?.value==='synthetic-demo'`);
await evaluate(`window.failHistory=true;[...document.querySelectorAll('button')].find(b=>b.textContent==='Saved versions').click()`);
await wait(`!!document.querySelector('dialog[open] [role=alert]')`);
await evaluate(`window.failHistory=false;[...document.querySelectorAll('dialog[open] button')].find(b=>b.textContent==='Retry history').click()`);
await wait(`document.querySelectorAll('dialog[open] [data-revision-id]').length===4`);
assert.equal(await evaluate(`document.querySelector('dialog[open] [data-revision-id="${child.analysis_id}"]').textContent.includes('rec-02')`),true);
await openVersion(child.analysis_id);
await wait(hasActive(child.analysis_id));
await wait(`!document.querySelector('dialog[open]')`);
assert.equal(await evaluate(`document.querySelector('#live-question').value`),child.question);
assert.equal(await evaluate(`window.calls.some(c=>c.path.endsWith('/records/rec-02'))`),true);
await openDrawer();
assert.equal(await evaluate(`document.querySelectorAll('dialog[open] [data-revision-id]').length`),3);
assert.equal(await evaluate(`document.querySelector('dialog[open] select').value`),base.analysis_id);
await evaluate(`window.missingSource=true`);
await openVersion(base.analysis_id);
await wait(hasActive(base.analysis_id));await wait(`!document.querySelector('dialog[open]')`);
await evaluate(`document.querySelector('button[aria-label="Inspect source rec-02"]').click()`);
await wait(`!!document.querySelector('dialog[open]')`);
assert.equal(await evaluate(`document.querySelector('dialog[open]').textContent.includes('source is unavailable')`),true);
await evaluate(`document.querySelector('dialog[open]').close()`);
await openDrawer();
await evaluate(`window.failRevision=true;window.missingSource=false`);
await openVersion(child.analysis_id);
await wait(`!!document.querySelector('dialog[open] [role=alert]')`);
assert.equal(await evaluate(hasActive(base.analysis_id)),true);
await evaluate(`window.failRevision=false`);await openVersion(child.analysis_id);
await wait(hasActive(child.analysis_id));await wait(`!document.querySelector('dialog[open]')`);
await openDrawer();
await evaluate(`window.delayRevision=true`);await openVersion(base.analysis_id);
await wait(`document.querySelector('#live-question').disabled`);
await evaluate(`document.querySelector('dialog[open]').close();var s=document.querySelector('#live-case');s.value='case-b';s.dispatchEvent(new Event('change',{bubbles:true}))`);
await wait(`document.querySelector('#live-case').value==='case-b'&&!document.querySelector('main')`);
await new Promise(r=>setTimeout(r,1200));
assert.equal(await evaluate(`!!document.querySelector('main')`),false);
await evaluate(`window.delayRevision=false;[...document.querySelectorAll('button')].find(b=>b.textContent==='Saved versions').click()`);
await wait(`document.querySelector('dialog[open]').textContent.includes('No saved versions')`);
await evaluate(`document.querySelector('dialog[open]').close();var s=document.querySelector('#live-case');s.value='synthetic-demo';s.dispatchEvent(new Event('change',{bubbles:true}))`);
await openDrawer();await evaluate(`window.delaySource=true`);await openVersion(base.analysis_id);
await wait(`document.querySelector('#live-question').disabled`);
await evaluate(`document.querySelector('dialog[open]').close();var s=document.querySelector('#live-case');s.value='case-b';s.dispatchEvent(new Event('change',{bubbles:true}))`);
await new Promise(r=>setTimeout(r,1200));
assert.equal(await evaluate(`!!document.querySelector('main')`),false);
await evaluate(`window.delaySource=false;var s=document.querySelector('#live-case');s.value='synthetic-demo';s.dispatchEvent(new Event('change',{bubbles:true}))`);
await openDrawer();
await send('Emulation.setDeviceMetricsOverride',{width:390,height:844,deviceScaleFactor:1,mobile:true});
assert.equal(await evaluate(`document.querySelector('dialog[open]').scrollWidth<=document.querySelector('dialog[open]').clientWidth`),true);
await send('Input.dispatchKeyEvent',{type:'keyDown',key:'Escape',code:'Escape',windowsVirtualKeyCode:27});
await send('Input.dispatchKeyEvent',{type:'keyUp',key:'Escape',code:'Escape',windowsVirtualKeyCode:27});
await wait(`!document.querySelector('dialog[open]')`);
assert.equal(await evaluate(`document.activeElement.textContent`),'Saved versions');
assert.equal(await evaluate(`window.calls.every(c=>c.method==='GET')`),true);
console.log('Versions history/retry, investigation grouping, saved selection, excluded originals, source failure, revision failure preserving view, stale revision/source case switches, empty history, Escape/focus and mobile checks passed; GET requests only.');
} finally {ws.close()}
