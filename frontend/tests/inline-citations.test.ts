import assert from "node:assert/strict";
import { test } from "node:test";

import { anchorCitations, splitCitations, type ProsePart } from "../src/lib/inline-citations";

// Synthetic IDs shaped like the importer's, and prose shaped like a real model answer.
const KNOWN = ["case1-url-40", "case1-url-61", "case1-url-168", "case1-download-4"];

const render = (parts: ProsePart[]) =>
  parts.map((part) => ("ids" in part ? `[${part.ids.join(",")}]` : part.text)).join("");

test("record parentheticals become citations attached to the preceding word", () => {
  const text = 'searches for "cleaner" (record case1-url-40) - "cric info" (records case1-url-61 and case1-url-168).';
  assert.equal(
    render(splitCitations(text, KNOWN)),
    'searches for "cleaner"[case1-url-40] - "cric info"[case1-url-61,case1-url-168].',
  );
});

test("short pointer wording inside the parenthetical is dropped with it", () => {
  const text = '"tools nz" (tab URL in download record case1-download-4) - next';
  assert.equal(render(splitCitations(text, KNOWN)), '"tools nz"[case1-download-4] - next');
});

test("a parenthetical with real wording keeps the wording", () => {
  const text = "a download (the installer was saved to Downloads and opened twice, record case1-download-4) ok";
  assert.equal(
    render(splitCitations(text, KNOWN)),
    "a download (the installer was saved to Downloads and opened twice[case1-download-4]) ok",
  );
});

test("bare mentions are replaced with their record word, and adjacent ones merge", () => {
  const text = "See record case1-url-40 and case1-url-61 for details.";
  assert.equal(render(splitCitations(text, KNOWN)), "See[case1-url-40] and[case1-url-61] for details.");
  assert.deepEqual(splitCitations("case1-url-40 case1-url-61", KNOWN), [{ ids: ["case1-url-40", "case1-url-61"] }]);
});

test("unknown or partial IDs are left exactly as written", () => {
  const text = "Mentions case1-url-999, xcase1-url-40 and case1-url-40b but not real ones.";
  assert.deepEqual(splitCitations(text, KNOWN), [{ text }]);
});

test("a longer ID is not mistaken for a shorter one that prefixes it", () => {
  assert.deepEqual(splitCitations("(record case1-url-168)", ["case1-url-16", "case1-url-168"]), [{ ids: ["case1-url-168"] }]);
});

test("text without known IDs, or with no IDs to look for, is untouched", () => {
  assert.deepEqual(splitCitations("Plain answer.", KNOWN), [{ text: "Plain answer." }]);
  assert.deepEqual(splitCitations("Plain (record case1-url-40).", []), [{ text: "Plain (record case1-url-40)." }]);
});

test("citations are placed right after the words their records contain", () => {
  const text = 'Searches for "ccleaner", "cric info" and "image steganography download" before ccsetup552.exe was saved.';
  const parts = anchorCitations(splitCitations(text, KNOWN), [
    { text: "ccleaner", ids: ["url-40"] },
    { text: "cric info", ids: ["url-61", "url-168"] },
    { text: "image steganography download", ids: ["url-178"] },
    { text: "ccsetup552.exe", ids: ["dl-2"] },
  ]);
  assert.equal(
    render(parts),
    'Searches for "ccleaner"[url-40], "cric info"[url-61,url-168] and "image steganography download"[url-178] before ccsetup552.exe[dl-2] was saved.',
  );
});

test("only the first whole-word match is cited, and short or unmatched anchors do nothing", () => {
  const text = "meth and crystal meth, then crystal meth again; methane is unrelated.";
  const parts = anchorCitations([{ text }], [
    { text: "crystal meth", ids: ["a"] },
    { text: "methane x", ids: ["b"] },
    { text: "me", ids: ["c"] },
  ]);
  assert.equal(render(parts), "meth and crystal meth[a], then crystal meth again; methane is unrelated.");
});

test("an anchor does not repeat a citation the prose already has at that spot", () => {
  const parts = anchorCitations(splitCitations('"ccleaner" (record case1-url-40)', KNOWN), [
    { text: "ccleaner", ids: ["case1-url-40"] },
  ]);
  assert.equal(render(parts), '"ccleaner"[case1-url-40]');
});
