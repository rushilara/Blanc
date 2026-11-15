import "./dom-setup";
import assert from "node:assert/strict";
import { afterEach, test } from "node:test";
import { act, createElement, type ReactNode } from "react";
import { createRoot, type Root } from "react-dom/client";
import { SuggestedFollowups } from "../src/components/SuggestedFollowups";
import { parseRevision } from "../src/lib/live-api";
import revision from "../../fixtures/synthetic_analysis_revision.json";
import type { AnalysisRevision } from "../src/lib/analysis";

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

test("parseRevision keeps suggested_followups from the shared contract", () => {
  const parsed = parseRevision(revision, revision.case_id, revision.question);
  assert.deepEqual(parsed.suggested_followups, [
    "Did Casey or Jordan confirm the proposed meeting in any later message?",
    "What document did Alex ask Jordan to deliver?",
  ]);
  const absent = structuredClone(revision) as AnalysisRevision;
  delete absent.suggested_followups;
  assert.equal(parseRevision(absent, revision.case_id, revision.question).suggested_followups, undefined);
});

test("chips are labelled suggestions and only call onPick (never auto-run)", () => {
  const picked: string[] = [];
  const view = mount(createElement(SuggestedFollowups, {
    suggestions: ["Who is Jordan?", "Where did they meet?"],
    onPick: question => picked.push(question),
  }));
  assert.match(view.textContent ?? "", /Suggestions — not findings/);
  const buttons = [...view.querySelectorAll("button")];
  assert.equal(buttons.length, 2);
  click(buttons[0]);
  click(buttons[1]);
  assert.deepEqual(picked, ["Who is Jordan?", "Where did they meet?"]);
});

test("no chips when suggestions are missing or empty", () => {
  assert.equal(mount(createElement(SuggestedFollowups, { onPick: () => undefined })).textContent, "");
  assert.equal(mount(createElement(SuggestedFollowups, { suggestions: [], onPick: () => undefined })).textContent, "");
});
