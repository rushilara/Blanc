// Requires a dedicated Chrome --headless --remote-debugging-port=9225 session
// and the frontend preview on :3001. Uses synthetic API responses only.
import fs from 'node:fs';
import assert from 'node:assert/strict';
const fixture = JSON.parse(fs.readFileSync(new URL('../../fixtures/synthetic_analysis_revision.json', import.meta.url)));
const records = JSON.parse(fs.readFileSync(new URL('../../fixtures/synthetic_records.json', import.meta.url)));
const targets = await (await fetch('http://127.0.0.1:9225/json')).json();
const ws = new WebSocket(targets.find(t => t.type === 'page').webSocketDebuggerUrl);
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
try {
  await send('Page.enable');
  const script = await send('Page.addScriptToEvaluateOnNewDocument', { source: `
    const fixture = ${JSON.stringify(fixture)};
    const records = ${JSON.stringify(records)};
    const nativeFetch = window.fetch;
    window.fetch = async (url, init) => {
      const path = new URL(url, location.href).pathname;
      if (path === '/health') return Response.json({status:'ok',config:{}});
      if (path === '/cases') return Response.json([
        {case_id:fixture.case_id,title:'Synthetic API test',import_status:'ready'},
        {case_id:'case-other',title:'Another test case',import_status:'ready'}
      ]);
      if (path.endsWith('/analyses')) return Response.json({...fixture, question:JSON.parse(init.body).question});
      if (path.includes('/records/')) return Response.json({record:records.find(r=>r.record_id===decodeURIComponent(path.split('/').pop())),excerpt:null});
      if (path.endsWith('/search')) return new Promise(resolve => {
        window.releaseSearch = () => resolve(Response.json({case_id:'case-other',question:JSON.parse(init.body).question,
          records:[{record_id:'late-source',source_file:'late.txt',source_locator:'line 1',excerpts:[{text:'old case content',char_start:0,char_end:16}]}],
          dropped_by_limit:0,dropped_by_budget:0}));
      });
      return nativeFetch(url,init);
    };
  ` });
  await send('Page.navigate', { url: 'http://127.0.0.1:3001/live' });
  await wait(`document.querySelector('#live-case')?.value === ${JSON.stringify(fixture.case_id)}`);
  await evaluate(`document.querySelector('#live-question').focus()`);
  await send('Input.insertText', { text: fixture.question });
  await evaluate(`document.querySelector('button[value=analysis]').click()`);
  await wait(`!!document.querySelector('main') || !!document.querySelector('[role=alert]')`);
  assert.equal(await evaluate(`!!document.querySelector('[role=alert]')`), false);
  assert.equal(await evaluate(`document.querySelector('main').textContent.includes(${JSON.stringify(fixture.answer)})`), true);
  await evaluate(`document.querySelector('main button[aria-haspopup=dialog]').click()`);
  await wait(`!!document.querySelector('dialog[open]')`);
  assert.equal(await evaluate(`document.querySelector('dialog[open]').textContent.includes(${JSON.stringify(records[0].content)})`), true);
  await evaluate(`document.querySelector('dialog[open] button').click()`);
  await evaluate(`document.querySelector('#live-case').value='case-other';document.querySelector('#live-case').dispatchEvent(new Event('change',{bubbles:true}))`);
  await wait(`!document.querySelector('main')`);
  await evaluate(`document.querySelector('button[value=search]').click()`);
  await wait(`typeof window.releaseSearch === 'function'`);
  await evaluate(`document.querySelector('#live-case').value=${JSON.stringify(fixture.case_id)};document.querySelector('#live-case').dispatchEvent(new Event('change',{bubbles:true}));window.releaseSearch()`);
  await new Promise(resolve => setTimeout(resolve, 250));
  assert.equal(await evaluate(`document.querySelectorAll('article').length`), 0);
  assert.equal(await evaluate(`document.body.textContent.includes('old case content')`), false);
  assert.equal(await evaluate(`document.querySelector('button[value=search]').disabled`), false);
  await send('Page.removeScriptToEvaluateOnNewDocument', { identifier: script.identifier });
  console.log('PASS: schema-valid live analysis uses the shared renderer and actual source controls; case changes clear it and discard stale search responses. Synthetic API mocks only.');
} finally { ws.close(); }
