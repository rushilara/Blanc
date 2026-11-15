import { test } from "node:test";
import assert from "node:assert/strict";
import { contextLine, orderNote, type FileContext } from "../src/lib/record-context";
import type { EvidenceRecord } from "../src/lib/analysis";

const base = { case_id: "case-test", timestamp: null } as const;

test("a Discord message becomes a chat line with sender, text, time and attachment", () => {
  const record: EvidenceRecord = { ...base, record_id: "d1", source_file: "Users/S/AppData/Roaming/Discord/Cache/data_2",
    source_locator: "gzip stream; Discord message id 1", timestamp: "2019-01-31T21:16:21.047000+00:00",
    content: JSON.stringify({ author: { username: "crayfish1980" }, content: "Heard of steganography?", attachments: [{ filename: "a.png" }] }) };
  const original = record.content;
  assert.deepEqual(contextLine(record), { style: "chat", who: "crayfish1980", text: "Heard of steganography?", time: "2019-01-31 21:16 UTC", note: "Attachment: a.png" });
  assert.equal(record.content, original);
});

test("a browser row shows its own name and never turns raw browser time into a date", () => {
  const line = contextLine({ ...base, record_id: "u1", source_file: "Users/S/AppData/Local/Google/Chrome/User Data/Default/History",
    source_locator: "SQLite urls.id=9", content: JSON.stringify({ title: "Download Image Steganography", url: "https://sourceforge.net/x", last_visit_time: 13193453785132940 }) });
  assert.equal(line.style, "row");
  assert.equal(line.time, null);
  assert.ok(line.style === "row" && line.title.includes("Image Steganography") && line.detail === "https://sourceforge.net/x");
});

test("generic JSON chat, metadata senders, media and plain text all get a line", () => {
  const json = contextLine({ ...base, record_id: "c", source_file: "chat.json", source_locator: "item 3", content: JSON.stringify({ from: "Alex", text: "Friday works." }) });
  assert.deepEqual(json, { style: "chat", who: "Alex", text: "Friday works.", time: null });
  const email = contextLine({ ...base, record_id: "e", source_file: "mailbox.eml", source_locator: "message 1", content: "Body", metadata: { sender: "Jordan" } });
  assert.equal(email.style === "chat" && email.who, "Jordan");
  const image = contextLine({ ...base, record_id: "i", source_file: "x.jpg", source_locator: "whole file", content: "", modality: "image", media_path: "media/abc/x.jpg" });
  assert.deepEqual(image, { style: "row", title: "x.jpg", kind: "Image", time: null });
  const text = contextLine({ ...base, record_id: "t", source_file: "notes.txt", source_locator: "line 4", content: "first line\nsecond" });
  assert.deepEqual(text, { style: "row", title: "first line", detail: "line 4", time: null });
});

test("long text is clipped for the list only", () => {
  const long = "x".repeat(2000);
  const line = contextLine({ ...base, record_id: "c", source_file: "chat.json", source_locator: "item 1", content: JSON.stringify({ sender: "A", body: long }) });
  assert.ok(line.style === "chat" && line.text.length === 600 && line.text.endsWith("…"));
});

test("the order note says how the file was ordered", () => {
  const context = (ordered_by: FileContext["ordered_by"]): FileContext => ({ source_file: "f", total: 2, index: 0, start: 0, ordered_by, items: [] });
  assert.match(orderNote(context("time")), /time order/);
  assert.match(orderNote(context("locator")), /position/);
  assert.match(orderNote(context("time_then_locator")), /then the rest/);
});
