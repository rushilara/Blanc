import { test } from "node:test";
import assert from "node:assert/strict";
import { presentSource } from "../src/lib/source-presentation";
import type { EvidenceRecord } from "../src/lib/analysis";

const record = (data: unknown, locator = "SQLite downloads.id=4"): EvidenceRecord => ({
  record_id: "source-4", case_id: "case-test", source_file: "Users/Test/Google/Chrome/User Data/Default/History",
  source_locator: locator, content: JSON.stringify(data), timestamp: null,
});
const download = { target_path: "C:\\Users\\Test\\Downloads\\Example Setup.exe",
  url_chain: [{ url: "https://example.test/Example%20Setup.exe" }], received_bytes: 1024, total_bytes: 2048,
  start_time: 13193453792736439, unexpected_field: "must remain in raw" };

test("extracts readable download fields without altering evidence or guessing dates/state", () => {
  const source = record(download); const original = source.content;
  const view = presentSource(source)!;
  assert.equal(view.title, "Browser download record");
  assert.ok(view.fields.some(f => f.label === "File name" && f.value === "Example Setup.exe"));
  assert.ok(view.fields.some(f => f.label === "Saved location" && f.value === download.target_path));
  assert.ok(view.fields.some(f => f.value === "https://example.test/Example%20Setup.exe"));
  assert.ok(view.fields.some(f => f.label === "Received bytes" && f.value === "1,024"));
  assert.ok(!view.fields.some(f => /time|date|success|execut/i.test(f.label)));
  assert.equal(source.content, original);
});

test("unknown, malformed, partial, or wrong-family sources keep the original view", () => {
  for (const data of [null, [], "text", {}, { ...download, url_chain: [null] }, { ...download, target_path: 42 }]) {
    assert.equal(presentSource(record(data)), null);
  }
  assert.equal(presentSource({ ...record(download), content: "{broken" }), null);
  assert.equal(presentSource({ ...record(download), source_file: "notes.txt" }), null);
  assert.equal(presentSource(record(download, "SQLite urls.id=4")), null);
});

test("history fields preserve URLs and do not interpret browser time integers", () => {
  const view = presentSource(record({ url: "https://example.test/a%20b", title: "Example", visit_count: 0, last_visit_time: 12345 }, "SQLite urls.id=1"))!;
  assert.deepEqual(view.fields, [{ label: "Page title", value: "Example" }, { label: "URL", value: "https://example.test/a%20b" }, { label: "Recorded visits", value: "0" }]);
  assert.equal(presentSource(record({ url: "https://example.test" }, "SQLite urls.id=1")), null);
});

test("Discord shows message/account text without treating embedded markup as HTML", () => {
  const source = { ...record({ author: { username: "account-one" }, content: '<img src=x onerror="alert(1)">\nmessage' }),
    source_file: "Users/Test/Discord/Cache/data_2", source_locator: "Discord message id 123" };
  const view = presentSource(source)!;
  assert.equal(view.title, "Discord message");
  assert.equal(view.fields[0].value, "account-one");
  assert.equal(view.message, '<img src=x onerror="alert(1)">\nmessage');
  assert.equal(presentSource({ ...source, content: JSON.stringify({ author: null, content: "test" }) }), null);
});

test("optional invalid byte values are not presented as exact file sizes", () => {
  const view = presentSource(record({ ...download, received_bytes: -1, total_bytes: Number.MAX_SAFE_INTEGER + 1 }))!;
  assert.ok(!view.fields.some(f => /bytes/.test(f.label)));
});

test("each record gets a short distinct label from its own fields", () => {
  const history = (url: string, title: string) =>
    presentSource(record({ url, title }, "SQLite urls.id=1"))!.label;
  assert.equal(presentSource(record(download))!.label, "Download: Example Setup.exe");
  assert.equal(history("https://www.google.com/search?q=test+tool&oq=x", "test tool - Google Search"), 'Search: "test tool"');
  assert.equal(history("https://www.google.co.nz/search?q=a%20b", "x"), 'Search: "a b"');
  assert.equal(history("https://www.google.com/maps/dir/A/B", "Google Maps"), "Page: Google Maps");
  assert.equal(history("https://example.test/page", ""), "Page: example.test");
  assert.equal(history("https://example.test/page", "x".repeat(80)).length, "Page: ".length + 48);
  const discord = { ...record({ author: { username: "account-one" }, content: "hi" }),
    source_file: "Users/Test/Discord/Cache/data_2", source_locator: "Discord message id 123" };
  assert.equal(presentSource(discord)!.label, "Discord: account-one");
});
