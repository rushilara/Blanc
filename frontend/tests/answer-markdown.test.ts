import "./dom-setup";
import assert from "node:assert/strict";
import { test } from "node:test";
import { act, createElement } from "react";
import { createRoot } from "react-dom/client";
import { AnswerText } from "../src/components/AnswerText";
import { parseAnswer, parseInline, plainAnswer } from "../src/lib/answer-markdown";

// The real answer from a live run (Owl, "Which Wi-Fi networks has this phone saved?").
const WIFI = "According to the saved Wi-Fi network records (`wifi/saved_networks.json`), the phone has saved the following three Wi-Fi networks:\n\n1. **attwifi** (Security mode: NONE, Network ID: 0)\n2. **iPhone** (Security mode: WPA-PSK, Network ID: 1)\n3. **chmc-guest** (Security mode: WPA-PSK, Network ID: 2)\n\nAll three network entries are associated with MAC address `34:4d:f7:54:20:bb`.";

test("a numbered Markdown answer becomes a paragraph, a list and a paragraph", () => {
  const blocks = parseAnswer(WIFI);
  assert.deepEqual(blocks.map(b => b.type), ["p", "ol", "p"]);
  const list = blocks[1];
  assert.ok(list.type === "ol");
  assert.deepEqual(list.items.map(item => item[0]), [
    { text: "attwifi", style: "strong" },
    { text: "iPhone", style: "strong" },
    { text: "chmc-guest", style: "strong" },
  ]);
  const first = blocks[0];
  assert.ok(first.type === "p");
  assert.deepEqual(first.content.find(part => part.style === "code"), { text: "wifi/saved_networks.json", style: "code" });
  assert.ok(!plainAnswer(WIFI).includes("**") && !plainAnswer(WIFI).includes("`"));
});

test("plain prose is untouched, including underscores and lone asterisks", () => {
  for (const text of [
    "According to an incoming SMS message from +13045184333, the first name of the phone's owner is Sarah.",
    "Records in sms/messages.json and wifi/saved_networks.json mention 2 * 3 devices.",
    "The file snake_case_name.txt was opened at 7 tonight.",
  ]) {
    assert.deepEqual(parseAnswer(text), [{ type: "p", content: [{ text }] }], text);
  }
});

test("bold, italic and code inline; unmatched markers stay as written", () => {
  assert.deepEqual(parseInline("a **b** *c* `d` e"), [
    { text: "a " }, { text: "b", style: "strong" }, { text: " " }, { text: "c", style: "em" },
    { text: " " }, { text: "d", style: "code" }, { text: " e" },
  ]);
  assert.deepEqual(parseInline("price **unclosed and *x"), [{ text: "price **unclosed and *x" }]);
});

test("bullets, headings, single newlines and wrapped list items", () => {
  const blocks = parseAnswer("## Summary\nLine one\nline two\n\n- first\n  continued\n- second");
  assert.deepEqual(blocks, [
    { type: "p", content: [{ text: "Summary", style: "strong" }] },
    { type: "p", content: [{ text: "Line one line two" }] },
    { type: "ul", items: [[{ text: "first continued" }], [{ text: "second" }]] },
  ]);
});

test("renders real elements, never HTML from the text, and keeps citations per run", () => {
  const host = document.createElement("div");
  const root = createRoot(host);
  act(() => root.render(createElement(AnswerText, {
    text: "Seen in **<img src=x onerror=alert(1)>** and:\n1. item rec-01",
    renderText: (value: string) => value.replace("rec-01", "[1]"),
  })));
  assert.equal(host.querySelector("img"), null, "markup in model text is shown as text");
  assert.equal(host.querySelector("strong")?.textContent, "<img src=x onerror=alert(1)>");
  assert.equal(host.querySelector("ol li")?.textContent, "item [1]");
  act(() => root.unmount());
});
