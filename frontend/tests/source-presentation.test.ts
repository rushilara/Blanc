import { test } from "node:test";
import assert from "node:assert/strict";
import { mapsDetails, presentSource } from "../src/lib/source-presentation";
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
  assert.deepEqual(view.fields, [{ label: "Page title", value: "Example" }, { label: "URL", value: "https://example.test/a%20b", link: "https://example.test/a%20b" }, { label: "Recorded visits", value: "0" }]);
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

test("each record is named by its own title, file name or sender, with its kind", () => {
  const history = (url: string, title: string) => presentSource(record({ url, title }, "SQLite urls.id=1"))!;
  const file = presentSource(record(download))!;
  assert.deepEqual([file.name, file.kind, file.anchors], ["Example Setup.exe", "Download · Chrome history", ["Example Setup.exe"]]);
  const fromSearch = presentSource(record({ ...download, tab_url: "https://www.google.com/search?q=example+tool&tbm=isch" }))!;
  assert.deepEqual(fromSearch.anchors, ["Example Setup.exe", "example tool"]);
  const search = history("https://www.google.com/search?q=test+tool&oq=x", "test tool - Google Search");
  assert.deepEqual([search.name, search.kind, search.anchors], ["test tool - Google Search", "Web search · Chrome history", ["test tool"]]);
  const page = history("https://www.google.com/maps/dir/A/B", "Google Maps");
  assert.deepEqual([page.name, page.kind, page.anchors], ["Route: A → B", "Google Maps · Chrome history", ["A", "B"]]);
  assert.equal(history("https://www.google.com/maps/place/Eastbourne+Library/@-41.29,174.89,15z", "Google Maps").name, "Eastbourne Library");
  assert.equal(history("https://www.google.com/maps/place/Eastbourne/@-41.29,174.89,15z", "Eastbourne - Google Maps").name, "Eastbourne - Google Maps");
  assert.equal(history("https://www.google.com/maps/@-40.79,175.31,5z", "Google Maps").name, "Google Maps");
  assert.equal(history("https://www.google.com/maps/dir//Eastbourne,+Lower+Hutt/@-41.2,174.9,13z", "Google Maps").name, "Directions to Eastbourne, Lower Hutt");
  const plain = history("https://example.test/about", "About us");
  assert.deepEqual([plain.kind, plain.anchors], ["Web page · Chrome history", []]);
  assert.equal(history("https://example.test/page", "").name, "example.test");
  assert.equal(history("https://example.test/page", "x".repeat(80)).name.length, 48);
  const discord = { ...record({ author: { username: "account-one" }, content: "hi" }),
    source_file: "Users/Test/Discord/Cache/data_2", source_locator: "Discord message id 123" };
  const message = presentSource(discord)!;
  assert.deepEqual([message.name, message.anchors], ["Message from account-one", ["account-one"]]);
});

test("Google Maps URLs are read into place, directions, centre, zoom and pin", () => {
  const place = mapsDetails("https://www.google.com/maps/place/Stokes+Valley,+Lower+Hutt+5019/@-40.7998944,175.3101282,5z/data=!4m5!3m4!1s0x6d40:0x500e!8m2!3d-41.1744!4d174.9787");
  assert.deepEqual(place.fields.map(f => [f.label, f.value]), [
    ["Place", "Stokes Valley, Lower Hutt 5019"],
    ["Map centre", "-40.7999, 175.3101"],
    ["Zoom", "5 (country or larger)"],
    ["Place pin", "-41.1744, 174.9787"],
  ]);
  assert.deepEqual(place.places, ["Stokes Valley, Lower Hutt 5019", "Stokes Valley"]);
  assert.ok(place.fields.every(f => f.derived));

  const dir = mapsDetails("https://www.google.com/maps/dir/Wainuiomata,+Lower+Hutt/Naenae,+Lower+Hutt/@-41.2,174.9,13z/data=!3m1");
  assert.deepEqual(dir.fields.map(f => [f.label, f.value]), [
    ["Directions", "Wainuiomata, Lower Hutt → Naenae, Lower Hutt"],
    ["Map centre", "-41.2000, 174.9000"],
    ["Zoom", "13 (suburb)"],
  ]);
  assert.deepEqual(mapsDetails("https://www.google.com/maps/search/petrol+station").fields, [{ label: "Map search", value: "petrol station", derived: true }]);
  assert.deepEqual(mapsDetails("https://www.google.com/maps").fields, []);
  assert.deepEqual(mapsDetails("https://example.test/maps/place/X/@1,2,3z").fields, []);
  assert.deepEqual(mapsDetails("not a url").fields, []);
});

test("a Maps history record gets its fields, a Maps kind, and its places as anchors", () => {
  const view = presentSource(record({ url: "https://www.google.com/maps/place/Eastbourne+Library/@-41.29,174.89,15z", title: "Eastbourne Library - Google Maps" }, "SQLite urls.id=9"))!;
  assert.equal(view.kind, "Google Maps · Chrome history");
  assert.deepEqual(view.anchors, ["Eastbourne Library"]);
  assert.equal(view.fields.find(f => f.label === "URL")?.link, "https://www.google.com/maps/place/Eastbourne+Library/@-41.29,174.89,15z");
  assert.ok(view.fields.some(f => f.label === "Place" && f.value === "Eastbourne Library"));
});
