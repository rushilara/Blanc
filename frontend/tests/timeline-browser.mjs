// Requires a dedicated Chrome --headless --remote-debugging-port=9226 session
// and the frontend preview on :3001. Uses synthetic API responses only.
import fs from 'node:fs';
import assert from 'node:assert/strict';
const fixture = JSON.parse(fs.readFileSync(new URL('../../fixtures/synthetic_analysis_revision.json', import.meta.url)));
const records = JSON.parse(fs.readFileSync(new URL('../../fixtures/synthetic_records.json', import.meta.url)));
const tab = await (await fetch('http://127.0.0.1:9226/json/new?about:blank', { method: 'PUT' })).json();
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
const event = (id, timestamp, precision) => ({ id, description: `Synthetic ${id} event`, timestamp,
  ...(precision ? { timestamp_precision: precision } : {}), timestamp_kind: 'source_stated',
  evidence_ids: ['rec-01', 'rec-02'], status: 'reported' });
fixture.timeline_events = [event('late', '2026-04-10T10:00:00Z'), event('early', '2026-04-10T11:00:00+02:00'),
  event('day', '2026-04-09', 'exact'), event('month', '2026-03', 'month'), event('approximate', '2026', 'approximate'),
  event('local', '2026-04-10T08:00:00'), event('missing', null), event('ambiguous', '10/04/2026')];
try {
  await send('Page.enable');
  await send('Emulation.setFocusEmulationEnabled', { enabled: true });
  await send('Emulation.setDeviceMetricsOverride', { width: 1440, height: 1100, deviceScaleFactor: 1, mobile: false });
  const script = await send('Page.addScriptToEvaluateOnNewDocument', { source: `
    const fixture=${JSON.stringify(fixture)}, records=${JSON.stringify(records)}, nativeFetch=window.fetch;
    window.normalizeCalls=0;
    window.fetch=async(url,init)=>{
      const path=new URL(url,location.href).pathname;
      if(path==='/health')return Response.json({status:'ok',config:{}});
      if(path==='/cases')return Response.json([{case_id:fixture.case_id,title:'Synthetic timeline test',import_status:'ready'}]);
      if(path.endsWith('/analyses'))return Response.json({...fixture,question:JSON.parse(init.body).question,timeline_events:window.emptyTimeline?[]:fixture.timeline_events});
      if(path.includes('/records/'))return Response.json({record:records.find(r=>r.record_id===decodeURIComponent(path.split('/').pop())),excerpt:null});
      if(path==='/timeline/normalize'){
        window.normalizeCalls++;
        if(window.failTimeline)return new Response('unavailable',{status:503});
      }
      return nativeFetch(url,init);
    };
  ` });
  await send('Page.navigate', { url: 'http://127.0.0.1:3002/live' });
  await wait(`!!document.querySelector('#live-case')?.value`);
  await evaluate(`document.querySelector('#live-question').focus()`);
  await send('Input.insertText', { text: fixture.question });
  await evaluate(`document.querySelector('button[value=analysis]').click()`);
  await wait(`document.querySelector('#dated-events')?.parentElement.querySelectorAll('li[data-event-id]').length===4`);
  const ids = heading => evaluate(`[...document.querySelector('#${heading}').parentElement.querySelectorAll('li[data-event-id]')].map(e=>e.dataset.eventId)`);
  assert.deepEqual(await ids('dated-events'), ['month', 'day', 'early', 'late']);
  assert.deepEqual(await ids('uncertain-events'), ['approximate', 'local', 'ambiguous', 'missing']);
  assert.equal(await evaluate(`document.querySelector('[data-event-id=day]').textContent.includes('Day only')`), true);
  assert.equal(await evaluate(`document.querySelector('[data-event-id=month]').textContent.includes('Month only')`), true);
  assert.equal(await evaluate(`document.querySelector('[data-event-id=early]').textContent.includes('2026-04-10T09:00:00Z')`), true);
  assert.equal(await evaluate(`document.querySelector('[data-event-id=local]').textContent.includes('Time zone unknown')`), true);
  assert.equal(await evaluate(`document.querySelector('[data-event-id=local]').textContent.includes('08:00:00Z')`), false);
  await evaluate(`document.querySelector('[data-event-id=early] button[aria-haspopup=dialog]').focus();document.querySelector('[data-event-id=early] button[aria-haspopup=dialog]').click()`);
  await wait(`!!document.querySelector('dialog[open]')`);
  assert.equal(await evaluate(`document.querySelector('dialog[open]').textContent.includes(${JSON.stringify(records[0].content)})`), true);
  await evaluate(`document.querySelector('dialog[open] nav button:last-child').click()`);
  assert.equal(await evaluate(`document.querySelector('dialog[open]').textContent.includes(${JSON.stringify(records[1].content)})`), true);
  await send('Input.dispatchKeyEvent', { type: 'keyDown', key: 'Escape', code: 'Escape', windowsVirtualKeyCode: 27, nativeVirtualKeyCode: 27 });
  await send('Input.dispatchKeyEvent', { type: 'keyUp', key: 'Escape', code: 'Escape', windowsVirtualKeyCode: 27, nativeVirtualKeyCode: 27 });
  await wait(`!document.querySelector('dialog[open]')`);
  assert.match(await evaluate(`document.activeElement.getAttribute('aria-label')`), /Inspect evidence for event/);
  await evaluate(`document.querySelector('[aria-labelledby=timeline-title]').scrollIntoView()`);
  let shot = await send('Page.captureScreenshot', { format: 'png' });
  fs.writeFileSync('/tmp/blanc-bla39-timeline-desktop.png', Buffer.from(shot.data, 'base64'));
  await send('Emulation.setTimezoneOverride', { timezoneId: 'Pacific/Auckland' });
  assert.equal(await evaluate(`document.querySelector('[data-event-id=early]').textContent.includes('2026-04-10T09:00:00Z')`), true);
  await send('Emulation.setDeviceMetricsOverride', { width: 390, height: 1100, deviceScaleFactor: 1, mobile: true });
  await evaluate(`document.querySelector('[aria-labelledby=timeline-title]').scrollIntoView()`);
  assert.equal(await evaluate(`document.documentElement.scrollWidth`), 390);
  shot = await send('Page.captureScreenshot', { format: 'png' });
  fs.writeFileSync('/tmp/blanc-bla39-timeline-mobile.png', Buffer.from(shot.data, 'base64'));
  await evaluate(`window.failTimeline=true;document.querySelector('button[value=analysis]').click()`);
  await wait(`document.querySelector('[role=alert]')?.textContent.includes('Could not verify event times')`);
  assert.equal((await ids('dated-events')).length, 0);
  assert.equal((await ids('uncertain-events')).length, 8);
  await evaluate(`window.failTimeline=false;document.querySelector('[role=alert] button').click()`);
  await wait(`document.querySelector('#dated-events')?.parentElement.querySelectorAll('li[data-event-id]').length===4`);
  const calls = await evaluate(`window.normalizeCalls`);
  await evaluate(`window.emptyTimeline=true;document.querySelector('button[value=analysis]').click()`);
  await wait(`document.querySelector('[aria-labelledby=timeline-title]')?.textContent.includes('No supported events')`);
  assert.equal(await evaluate(`window.normalizeCalls`), calls);
  await send('Page.removeScriptToEvaluateOnNewDocument', { identifier: script.identifier });
  console.log('PASS: real normalization adapter orders offsets; partial/uncertain/undated grouping; multi-source event drawer; Escape and focus; timezone-independent text; 390px no overflow; failure keeps every event, retry restores order; empty timeline skips API. Synthetic analysis/source fixtures only.');
} finally { ws.close(); await fetch(`http://127.0.0.1:9226/json/close/${tab.id}`); }
