import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { createContext, runInContext } from "node:vm";

function renderer() {
  const elements = new Map<string, ReturnType<typeof element>>();
  function element() {
    const classes = new Set<string>();
    return {
      value: "", textContent: "", innerHTML: "", style: {} as Record<string, string>,
      classList: { add: (s: string) => classes.add(s), remove: (s: string) => classes.delete(s), contains: (s: string) => classes.has(s), toggle: (s: string) => classes.has(s) ? classes.delete(s) : classes.add(s) },
      getBoundingClientRect: () => ({ width: 1400, height: 650, left: 0, top: 0 }),
      getContext: () => new Proxy({ measureText: (s: string) => ({ width: s.length * 6 }) }, { get: (o, key) => o[key as keyof typeof o] ?? (() => {}) }),
      querySelector: () => ({}), querySelectorAll: () => [], append() {}, addEventListener() {}, setPointerCapture() {},
    };
  }
  const get = (s: string) => { if (!elements.has(s)) elements.set(s, element()); return elements.get(s)!; };
  get("#filter").value = "all";
  let receive: (event: unknown) => void = () => {};
  const parent = { postMessage() {} };
  const origin = "http://localhost:3000";
  const context = createContext({ document: { querySelector: get, createElement: element, addEventListener() {} }, window: { addEventListener: (_: string, fn: typeof receive) => { receive = fn; } }, parent, location: { origin }, devicePixelRatio: 1, matchMedia: () => ({ matches: false }), ResizeObserver: class { observe() {} }, requestAnimationFrame() {} });
  runInContext(readFileSync(new URL("../public/blanc/connection-label-layout.js", import.meta.url), "utf8"), context);
  const html = readFileSync(new URL("../public/blanc/connections.html", import.meta.url), "utf8");
  runInContext(html.match(/<script>([\s\S]*)<\/script>/)![1], context);
  const graph = {
    nodes: [{ id: "n1", label: '<img src=x onerror="bad()">', type: "account", evidence_ids: ["r1", "r1", "r2"] }, { id: "n2", label: "Device", type: "device" }],
    edges: [{ id: "e", source: "n1", target: "n2", type: "accessed", status: "inferred", evidence_ids: ["r1", "r2"], description: "<script>bad()</script>" }],
  };
  return { get, context, graph, receive: (data: unknown, overrides = {}) => receive({ source: parent, origin, data, ...overrides }) };
}

test("spatial renderer accepts only its same-origin parent and never fills in demo data", () => {
  const r = renderer();
  r.receive({ type: "blanc:graph", graph: r.graph }, { origin: "https://other.example" });
  runInContext("render()", r.context);
  assert.equal(r.get("#count").textContent, "0 entities · 0 relationships");
  r.receive({ type: "blanc:graph", graph: r.graph });
  runInContext("render()", r.context);
  assert.equal(r.get("#count").textContent, "2 entities · 1 relationships");
});

test("evidence inspection escapes untrusted text and withholds excluded source links", () => {
  const r = renderer();
  r.receive({ type: "blanc:graph", graph: r.graph, excludedRecordIds: ["r2"] });
  runInContext("inspect('n1')", r.context);
  const node = r.get("#panel").innerHTML;
  assert.match(node, /&lt;img/);
  assert.equal(node.match(/data-source="r1"/g)?.length, 1);
  assert.doesNotMatch(node, /data-source="r2"/);
  runInContext("inspectEdge(0)", r.context);
  assert.match(r.get("#panel").innerHTML, /&lt;script&gt;/);
  assert.match(r.get("#panel").innerHTML, /inferred/);
  assert.match(r.get("#panel").innerHTML, /excluded/i);
});

test("new analyses replace prior entities and reset selection and filters", () => {
  const r = renderer();
  r.receive({ type: "blanc:graph", graph: r.graph });
  runInContext("inspect('n1')", r.context);
  r.get("#filter").value = "account";
  r.receive({ type: "blanc:graph", graph: { nodes: [], edges: [] } });
  runInContext("render()", r.context);
  assert.equal(r.get("#count").textContent, "0 entities · 0 relationships");
  assert.equal(r.get("#filter").value, "all");
  assert.equal(r.get("#panel").classList.contains("open"), false);
});

test("account handles get a short display name; the original label is kept", () => {
  const r = renderer();
  const d = (name: string, type = "account") =>
    runInContext(`BlancGraphLabels.describe(${JSON.stringify({ name, type })})`, r.context) as { title: string; kind: string; source: string; original: string };
  assert.equal(d("crayfish1980").title, "Crayfish");
  assert.equal(d("crayfish1980").original, "crayfish1980");
  assert.equal(d("heresjohnny1").title, "Heresjohnny");
  const mail = d("crayfish1980@protonmail.com");
  assert.equal(mail.title, "Crayfish");
  assert.equal(mail.kind, "Email account");
  assert.equal(mail.source, "Email · protonmail.com");
  assert.equal(mail.original, "crayfish1980@protonmail.com");
  assert.equal(d("Steve K").title, "Steve K");
  assert.equal(d("ab12").title, "ab12");
  assert.equal(d("crayfish1980", "location").title, "crayfish1980");
});

test("production label formatting keeps exact filenames behind readable display titles", () => {
  const r = renderer();
  const label = runInContext("BlancGraphLabels.describe({name:'Local File flightbookings.PNG',type:'document'})", r.context);
  assert.equal(label.title, "Flight booking");
  assert.equal(label.source, "flightbookings.PNG");
  assert.equal(label.original, "Local File flightbookings.PNG");
});

test("connection inspector shows original excerpts safely and withholds excluded records", () => {
  const r = renderer();
  r.receive({ type: "blanc:graph", graph: r.graph, records: [
    { record_id: "r1", source_file: "evidence.json", source_locator: "record 1", content: "<img src=x>original excerpt", timestamp: "2026-01-01" },
    { record_id: "r2", source_file: "excluded.json", source_locator: "record 2", content: "withheld body" },
  ], excludedRecordIds: ["r2"] });
  runInContext("inspectEdge(0)", r.context);
  const html = r.get("#panel").innerHTML;
  assert.match(html, /What this connection means/);
  assert.match(html, /&lt;img src=x&gt;original excerpt/);
  assert.doesNotMatch(html, /<img|withheld body/);
  assert.match(html, /evidence.json/);
  assert.match(html, /record 1/);
});
