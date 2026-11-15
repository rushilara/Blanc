import "./dom-setup";
import assert from "node:assert/strict";
import { afterEach, test } from "node:test";
import { act, createElement } from "react";
import { createRoot, type Root } from "react-dom/client";
import { groupInvestigations, investigationTitle } from "../src/lib/investigation-navigation";
import { InvestigationNavigation } from "../src/components/InvestigationNavigation";
import type { RevisionSummary } from "../src/lib/live-api";

const question = "Map the connections between the accounts and all their files. Explain each relationship and cite its sources.";
const root: RevisionSummary = { analysis_id: "root-a", parent_analysis_id: null, question, created_at: "2026-01-01T10:00:00Z", excluded_record_ids: [], needs_review: false, restored_from_analysis_id: null };
const another = { ...root, analysis_id: "root-b", created_at: "2026-01-02T10:00:00Z" };
const child = { ...root, analysis_id: "child", parent_analysis_id: "root-a", question: "What changed?", created_at: "2026-01-03T10:00:00Z", excluded_record_ids: ["source-1"] };
const history = [child, another, root];
let mounted: Root | undefined;
let container: HTMLDivElement | undefined;
afterEach(() => { act(() => mounted?.unmount()); container?.remove(); });

function mount() {
  const opened: string[] = [];
  container = document.createElement("div");document.body.append(container);
  mounted = createRoot(container);
  act(() => mounted!.render(createElement(InvestigationNavigation, { history, ready: true, activeId: child.analysis_id, busy: false, variant: "recent", onOpen: id => opened.push(id) })));
  // jsdom lacks the native dialog implementation. Browser focus/keyboard behavior
  // remains delegated to the platform; test our open/select/close transitions here.
  const dialog = container.querySelector("dialog")!;
  dialog.showModal = () => { dialog.open = true; };
  dialog.close = () => { dialog.open = false; };
  return { container, opened, dialog };
}
function click(el: Element | null) { assert.ok(el);act(() => el.dispatchEvent(new MouseEvent("click", { bubbles: true }))); }

test("grouping duplicate root prompts preserves independent sessions and every follow-up", () => {
  const groups = groupInvestigations(history);
  assert.equal(groups.length, 1);
  assert.equal(groups[0].sessions.length, 2);
  assert.equal(groups[0].versions.length, 3);
  assert.equal(groups[0].versions[0].analysis_id, "child");
  assert.deepEqual(groups[0].sessions.find(session => session.root.analysis_id === root.analysis_id)?.versions.map(v => v.analysis_id), ["root-a", "child"]);
  assert.equal(groups[0].question, question);
  assert.deepEqual(history.map(r => r.analysis_id), ["child", "root-b", "root-a"]);
});

test("generic connection titles do not assume a case contains Discord", () => {
  assert.equal(investigationTitle(question), "Connections");
  assert.equal(investigationTitle("Map the connections between Discord accounts."), "Discord connections");
  assert.ok(investigationTitle("An unusually long question ".repeat(8)).length <= 52);
});

test("recent list shows one compact investigation and opens its latest saved version", () => {
  const { container, opened } = mount();
  assert.equal(container.querySelectorAll(".blanc-investigation-items > li").length, 1);
  assert.equal(container.querySelector(".blanc-investigation-open strong")?.textContent, "Connections");
  assert.equal(container.querySelector(".blanc-investigation-open")?.getAttribute("title"), question);
  click(container.querySelector(".blanc-investigation-open"));
  assert.deepEqual(opened, ["child"]);
});

test("History exposes both sessions and opens the exact chosen version without generating", () => {
  const { container, opened, dialog } = mount();
  click(container.querySelector(".blanc-investigation-history"));
  assert.equal(dialog.open, true);
  assert.equal(dialog.querySelectorAll(".blanc-history-session").length, 2);
  assert.equal(dialog.querySelectorAll(".blanc-history-version").length, 3);
  assert.ok(dialog.textContent?.includes(question));
  click(dialog.querySelector(".blanc-history-version > button"));
  assert.equal(dialog.open, false);
  assert.equal(opened.length, 1);
  assert.ok(history.some(version => version.analysis_id === opened[0]));
});

test("opening page uses a case picker and compact recents without eagerly loading evidence", async () => {
  const originalFetch = globalThis.fetch;
  const requests: string[] = [];
  const { Findings } = await import("../src/app/workspace/Findings");
  globalThis.fetch = async url => {
    const path = String(url); requests.push(path);
    if (path.endsWith("/cases")) return Response.json([{ case_id: "case-a", title: "Case A", import_status: "ready" }]);
    if (path.endsWith("/analyses")) return Response.json(history);
    throw new Error(`Unexpected eager request: ${path}`);
  };
  try {
    container = document.createElement("div"); document.body.append(container);
    mounted = createRoot(container);
    await act(async () => { mounted!.render(createElement(Findings)); });
    assert.equal(container.querySelector<HTMLSelectElement>('[aria-label="Choose a case"]')?.value, "case-a");
    assert.equal(container.querySelectorAll(".blanc-investigation-items > li").length, 1);
    assert.ok(container.textContent?.includes("Recent investigations"));
    assert.ok(container.textContent?.includes("Browse case evidence"));
    assert.ok(!container.textContent?.includes(question));
    assert.equal(requests.some(url => url.includes("/records")), false);
  } finally { globalThis.fetch = originalFetch; }
});
