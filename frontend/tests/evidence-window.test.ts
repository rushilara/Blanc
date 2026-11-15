import "./dom-setup";
import assert from "node:assert/strict";
import { afterEach, test } from "node:test";
import { act, createElement, type ReactNode } from "react";
import { createRoot, type Root } from "react-dom/client";
import { EvidenceWindowNotice, FollowUpWindowChoice } from "../src/components/EvidenceWindowNotice";
import type { RetrievalCoverage } from "../src/lib/analysis";
import { budgetNote, offerCopy, skippedLines, stillLeftOutCopy, windowLabel } from "../src/lib/evidence-window";

const coverage = (patch: Partial<RetrievalCoverage>): RetrievalCoverage => ({
  evidence_window: "standard",
  max_records: 16,
  max_excerpt_chars: 32000,
  skipped: [],
  budget_substituted: false,
  left_out_record_ids: [],
  offer_expanded_window: false,
  ...patch,
});

let mounted: Root | undefined;
let container: HTMLDivElement | undefined;
afterEach(() => { act(() => mounted?.unmount()); container?.remove(); });

function mount(node: ReactNode) {
  act(() => mounted?.unmount());
  container?.remove();
  container = document.createElement("div");
  document.body.append(container);
  mounted = createRoot(container);
  act(() => mounted!.render(node));
  return container;
}
function click(el: Element | null) {
  assert.ok(el);
  act(() => el.dispatchEvent(new MouseEvent("click", { bubbles: true })));
}

test("the window label comes from the revision, and older revisions stay standard", () => {
  assert.equal(windowLabel("standard", 16), "Standard · 16 records");
  assert.equal(windowLabel("expanded", 24), "Expanded · 24 records");
  assert.equal(windowLabel(undefined, undefined), "Standard");
});

test("the offer is the server flag, and the other notes never become a prompt", () => {
  const offered = coverage({ left_out_record_ids: ["a", "b", "c", "d", "e"], offer_expanded_window: true });
  assert.equal(offerCopy(offered), "5 more relevant records were left out of this answer. Want more evidence?");
  assert.equal(offerCopy(coverage({ left_out_record_ids: ["a"], offer_expanded_window: true })), "1 more relevant record was left out of this answer. Want more evidence?");
  assert.equal(offerCopy(coverage({ skipped: [{ record_id: "a", rank: 17, reason: "record_limit" }] })), null);
  assert.equal(stillLeftOutCopy(coverage({ evidence_window: "expanded", max_records: 24, left_out_record_ids: ["a", "b", "c"] })), "3 relevant records were still left out.");
  assert.equal(stillLeftOutCopy(offered), null);
  assert.equal(budgetNote(coverage({ skipped: [{ record_id: "a", rank: 2, reason: "budget" }] })), "Some evidence didn't fit in this answer.");
  assert.equal(budgetNote(coverage({ skipped: [{ record_id: "a", rank: 2, reason: "budget" }], offer_expanded_window: true, left_out_record_ids: ["b"] })), null);
  assert.equal(budgetNote(coverage({ skipped: [{ record_id: "a", rank: 2, reason: "record_limit" }] })), null);
});

test("skipped records are ids and ranks, with their content never included", () => {
  const lines = skippedLines(coverage({ skipped: [
    { record_id: "rec-b", rank: 18, reason: "record_limit" },
    { record_id: "rec-a", rank: 3, reason: "budget" },
  ] }));
  assert.deepEqual(lines, [
    { recordId: "rec-a", rank: 3, why: "didn't fit the character budget" },
    { recordId: "rec-b", rank: 18, why: "ranked past the record limit" },
  ]);
  assert.deepEqual(Object.keys(lines[0]), ["recordId", "rank", "why"]);
});

test("media skips say why in plain words and name the file type (BLA-73)", () => {
  const lines = skippedLines(coverage({ skipped: [
    { record_id: "img-1", rank: 2, reason: "media_limit", modality: "image" },
    { record_id: "pdf-1", rank: 4, reason: "media_too_large", modality: "pdf" },
    { record_id: "img-2", rank: 5, reason: "media", modality: "image" },
  ] }));
  assert.deepEqual(lines.map(l => [l.recordId, l.why, l.modality]), [
    ["img-1", "left out: media cap reached", "image"],
    ["pdf-1", "file too large to send", "pdf"],
    ["img-2", "file missing or failed integrity check", "image"],
  ]);
  const view = mount(createElement(EvidenceWindowNotice, {
    coverage: coverage({ skipped: [{ record_id: "pdf-1", rank: 4, reason: "media_too_large", modality: "pdf" }] }),
    busy: false,
    onExpand: () => {},
  }));
  assert.equal(view.querySelector(".blanc-skipped")?.textContent, "pdf-1 (PDF) · rank 4 · file too large to send");
});

test("an unknown reason from a newer server still gets a line", () => {
  const odd = coverage({ skipped: [{ record_id: "x", rank: 1, reason: "something_new" as never }] });
  assert.deepEqual(skippedLines(odd), [{ recordId: "x", rank: 1, why: "left out" }]);
});

test("the prompt shows every trade-off before Yes, and No changes nothing", () => {
  const expanded: string[] = [];
  const view = mount(createElement(EvidenceWindowNotice, {
    coverage: coverage({ left_out_record_ids: ["rec-hidden-body"], offer_expanded_window: true, skipped: [{ record_id: "rec-hidden-body", rank: 17, reason: "record_limit" }] }),
    busy: false,
    onExpand: () => expanded.push("yes"),
  }));
  const text = view.textContent ?? "";
  assert.match(text, /1 more relevant record was left out of this answer\. Want more evidence\?/);
  for (const title of ["Slower answer.", "Can be less precise.", "More evidence text sent to Gemini."]) assert.ok(text.includes(title), title);
  const yes = view.querySelector("button.blanc-evidence-yes");
  const tradeoffs = view.querySelector("ul");
  assert.equal(yes?.getAttribute("type"), "submit");
  assert.equal(yes?.hasAttribute("disabled"), false);
  assert.ok(tradeoffs && yes && (tradeoffs.compareDocumentPosition(yes) & Node.DOCUMENT_POSITION_FOLLOWING));
  assert.equal(view.querySelector(".blanc-skipped")?.textContent, "rec-hidden-body · rank 17 · ranked past the record limit");
  assert.equal(view.querySelector(".blanc-skipped button, .blanc-skipped a"), null);
  click(view.querySelector("button.blanc-evidence-no"));
  assert.deepEqual(expanded, []);
  assert.equal(view.querySelector("button.blanc-evidence-yes"), null);
  assert.match(view.textContent ?? "", /rank 17/);
});

test("Yes asks for the expanded window, and a declined prompt stays declined only for that render", () => {
  const expanded: string[] = [];
  const props = {
    coverage: coverage({ left_out_record_ids: ["a"], offer_expanded_window: true }),
    busy: false,
    onExpand: () => expanded.push("yes"),
  };
  const view = mount(createElement(EvidenceWindowNotice, props));
  const form = view.querySelector("form");
  assert.ok(form);
  act(() => { form.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true })); });
  assert.deepEqual(expanded, ["yes"]);
});

test("still-left-out and budget-only notes have no next step", () => {
  const still = mount(createElement(EvidenceWindowNotice, {
    coverage: coverage({ evidence_window: "expanded", max_records: 24, left_out_record_ids: ["a", "b", "c"], offer_expanded_window: false }),
    busy: false,
    onExpand: () => assert.fail("no further step"),
  }));
  assert.match(still.textContent ?? "", /3 relevant records were still left out/);
  assert.equal(still.querySelector("button"), null);

  const budget = mount(createElement(EvidenceWindowNotice, {
    coverage: coverage({ skipped: [{ record_id: "rec-fit", rank: 4, reason: "budget" }] }),
    busy: false,
    onExpand: () => assert.fail("budget skips are not an offer"),
  }));
  assert.match(budget.textContent ?? "", /Some evidence didn't fit in this answer/);
  assert.match(budget.textContent ?? "", /rec-fit · rank 4/);
  assert.equal(budget.querySelector("button"), null);
});

test("a follow-up inherits the expanded window and can switch back to standard", () => {
  const choices: string[] = [];
  const hidden = mount(createElement(FollowUpWindowChoice, {
    coverage: coverage({}),
    value: "inherit",
    onChange: value => choices.push(value),
  }));
  assert.equal(hidden.querySelector("fieldset"), null);

  const view = mount(createElement(FollowUpWindowChoice, {
    coverage: coverage({ evidence_window: "expanded", max_records: 24 }),
    value: "inherit",
    onChange: value => choices.push(value),
  }));
  const radios = view.querySelectorAll<HTMLInputElement>("input[type=radio]");
  assert.equal(radios.length, 2);
  assert.equal(radios[0].checked, true);
  assert.match(view.textContent ?? "", /Expanded · 24 records/);
  assert.match(view.textContent ?? "", /Standard window/);
  act(() => { radios[1].click(); });
  assert.deepEqual(choices, ["standard"]);
});
