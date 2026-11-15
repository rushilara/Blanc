// Requires a dedicated Chrome --headless --remote-debugging-port=9227 session
// and the frontend (`next start`) on :3003. Override with CDP_PORT / APP_URL.
// Uses synthetic API responses only.
import fs from 'node:fs';
import assert from 'node:assert/strict';
const cdp = `http://127.0.0.1:${process.env.CDP_PORT ?? 9227}`;
const app = process.env.APP_URL ?? 'http://127.0.0.1:3003';
const fixture = JSON.parse(fs.readFileSync(new URL('../../fixtures/synthetic_analysis_revision.json', import.meta.url)));
const records = JSON.parse(fs.readFileSync(new URL('../../fixtures/synthetic_records.json', import.meta.url)));
// rec-03 is cited only by the Dana node; a second Alex→Jordan edge runs parallel to edge-1.
records.push({ ...records[0], record_id: 'rec-03', source_file: 'notes.txt', content: 'Synthetic note that names Dana.', metadata: {} });
fixture.retrieved_record_ids = ['rec-01', 'rec-02', 'rec-03'];
fixture.graph.nodes.push({ id: 'dana', label: 'Dana', type: 'person', evidence_ids: ['rec-03'] });
fixture.graph.edges.push({ id: 'edge-1b', source: 'alex', target: 'jordan', type: 'paid', evidence_ids: ['rec-02'], status: 'observed', description: 'Synthetic payment' });

const tab = await (await fetch(`${cdp}/json/new?about:blank`, { method: 'PUT' })).json();
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
  if (result.exceptionDetails) throw new Error(result.exceptionDetails.exception?.description ?? result.exceptionDetails.text);
  return result.result.value;
};
const wait = async expression => {
  for (let n = 0; n < 100; n++) {
    if (await evaluate(expression)) return;
    await new Promise(resolve => setTimeout(resolve, 100));
  }
  throw new Error(`Timed out: ${expression}`);
};
const center = selector => evaluate(`(()=>{const r=document.querySelector(${JSON.stringify(selector)}).getBoundingClientRect();return {x:r.x+r.width/2,y:r.y+r.height/2}})()`);
const click = async selector => {
  const { x, y } = await center(selector);
  for (const type of ['mouseMoved', 'mousePressed', 'mouseReleased']) {
    await send('Input.dispatchMouseEvent', { type, x, y, button: 'left', clickCount: 1 });
  }
};
const key = async (key, code, keyCode) => {
  for (const type of ['keyDown', 'keyUp']) await send('Input.dispatchKeyEvent', { type, key, code, windowsVirtualKeyCode: keyCode, nativeVirtualKeyCode: keyCode });
};
const dialogText = `document.querySelector('dialog[open]')?.textContent ?? ''`;
const closeDialog = async () => { await evaluate(`document.querySelector('dialog[open] button').click()`); await wait(`!document.querySelector('dialog[open]')`); };
const counts = `({nodes:document.querySelectorAll('.react-flow__node').length,edges:document.querySelectorAll('.react-flow__edge').length})`;

try {
  await send('Page.enable');
  await send('Emulation.setFocusEmulationEnabled', { enabled: true });
  await send('Emulation.setDeviceMetricsOverride', { width: 1440, height: 1400, deviceScaleFactor: 1, mobile: false });
  await send('Page.addScriptToEvaluateOnNewDocument', { source: `
    const fixture=${JSON.stringify(fixture)}, records=${JSON.stringify(records)}, nativeFetch=window.fetch;
    window.recordRequests=[];
    window.fetch=async(url,init)=>{
      const path=new URL(url,location.href).pathname;
      if(path==='/health')return Response.json({status:'ok',config:{}});
      if(path==='/cases')return Response.json([{case_id:fixture.case_id,title:'Synthetic graph test',import_status:'ready'}]);
      if(path.endsWith('/analyses'))return Response.json({...fixture,question:JSON.parse(init.body).question});
      if(path.includes('/records/')){const id=decodeURIComponent(path.split('/').pop());window.recordRequests.push(id);return Response.json({record:records.find(r=>r.record_id===id),excerpt:null});}
      if(path==='/timeline/normalize')return new Response('unavailable',{status:503});
      return nativeFetch(url,init);
    };
  ` });
  await send('Page.navigate', { url: `${app}/live` });
  await wait(`!!document.querySelector('#live-case')?.value`);
  await evaluate(`document.querySelector('#live-question').focus()`);
  await send('Input.insertText', { text: fixture.question });
  await evaluate(`document.querySelector('button[value=analysis]').click()`);
  await wait(`document.querySelectorAll('.react-flow__node').length===4 && document.querySelectorAll('[data-edge-label]').length===3`);
  await new Promise(resolve => setTimeout(resolve, 600)); // fitView animation
  await evaluate(`document.querySelector('[aria-label="Entity relationship graph"]').scrollIntoView({block:'center'})`);

  // 1. Node-only citation is requested and opens in the drawer.
  assert.ok((await evaluate(`window.recordRequests`)).includes('rec-03'));
  await click('.react-flow__node[data-id=dana]');
  await wait(`!!document.querySelector('dialog[open]')`);
  assert.match(await evaluate(dialogText), /Synthetic note that names Dana/);
  assert.doesNotMatch(await evaluate(dialogText), /unavailable/);
  await closeDialog();

  // 2. Backspace / Delete on a selected, focused node removes nothing.
  const before = await evaluate(counts);
  await click('.react-flow__node[data-id=alex]');
  await wait(`!!document.querySelector('dialog[open]')`);
  await closeDialog();
  await evaluate(`document.querySelector('.react-flow__node[data-id=alex]').focus()`);
  assert.equal(await evaluate(`document.querySelector('.react-flow__node[data-id=alex]').classList.contains('selected')`), true);
  await key('Backspace', 'Backspace', 8);
  await key('Delete', 'Delete', 46);
  await new Promise(resolve => setTimeout(resolve, 200));
  assert.deepEqual(await evaluate(counts), before);
  // Enter on a focused node opens its sources (keyboard path).
  await key('Enter', 'Enter', 13);
  await wait(`!!document.querySelector('dialog[open]')`);
  assert.match(await evaluate(dialogText), new RegExp(records[0].content));
  await closeDialog();

  // 3. Parallel edges: distinct paths and labels, each reachable and opening its own sources.
  const d = id => evaluate(`document.querySelector('.react-flow__edge[data-id="${id}"] path.react-flow__edge-path').getAttribute('d')`);
  assert.notEqual(await d('edge-1'), await d('edge-1b'));
  const box = id => evaluate(`JSON.parse(JSON.stringify(document.querySelector('[data-edge-label=${id}]').getBoundingClientRect()))`);
  const [b1, b2] = [await box('edge-1'), await box('edge-1b')];
  const overlap = b1.left < b2.right && b2.left < b1.right && b1.top < b2.bottom && b2.top < b1.bottom;
  assert.equal(overlap, false, 'parallel edge labels overlap');
  // Nothing covers either label: the topmost element at each centre is that label.
  for (const id of ['edge-1', 'edge-1b']) {
    assert.equal(await evaluate(`(()=>{const c=document.querySelector('[data-edge-label=${id}]').getBoundingClientRect();return document.elementFromPoint(c.x+c.width/2,c.y+c.height/2).closest('[data-edge-label]')?.dataset.edgeLabel})()`), id);
  }
  for (const [label, edge, content] of [['edge-1', 'edge-1b', records[1].content], ['edge-1b', 'edge-1', records[0].content]]) {
    await click(`[data-edge-label=${label}]`);
    await wait(`document.querySelectorAll('[data-pair-edge]').length===2`);
    await evaluate(`document.querySelector('[data-pair-edge=${edge}] button').click()`);
    await wait(`!!document.querySelector('dialog[open]')`);
    assert.match(await evaluate(dialogText), new RegExp(content));
    await closeDialog();
  }
  // A lone edge's label opens its sources directly.
  await click('[data-edge-label=edge-2]');
  await wait(`!!document.querySelector('dialog[open]')`);
  assert.match(await evaluate(dialogText), new RegExp(records[1].content));
  await closeDialog();
  // Accessible names describe the action each label performs.
  assert.equal(await evaluate(`document.querySelector('[data-edge-label=edge-1]').getAttribute('aria-label')`), 'Choose among 2 relationships between Alex and Jordan');
  assert.equal(await evaluate(`document.querySelector('[data-edge-label=edge-2]').getAttribute('aria-label')`), 'Open 1 source for Jordan communicated with Casey (reported)');
  // Status is shown as text, and every relationship is listed with source buttons.
  assert.equal(await evaluate(`document.querySelectorAll('[aria-label=Relationships] li[data-edge-id]').length`), 3);
  assert.match(await evaluate(`document.querySelector('[data-edge-label=edge-1b]').textContent`), /observed/);
  assert.deepEqual(await evaluate(counts), before);
  const shot = await send('Page.captureScreenshot', { format: 'png' });
  fs.writeFileSync(process.env.SHOT ?? '/tmp/blanc-bla38-graph.png', Buffer.from(shot.data, 'base64'));
  console.log('PASS: node-only citation loaded and shown; Backspace/Delete remove nothing; Enter opens node sources; parallel edges have distinct paths/labels, neither label covered, each opens its own sources; lone edge opens directly. Synthetic fixtures only.');
} finally { ws.close(); await fetch(`${cdp}/json/close/${tab.id}`); }
