import "./dom-setup";
import assert from "node:assert/strict";
import { afterEach, test } from "node:test";
import { act, createElement } from "react";
import { createRoot, type Root } from "react-dom/client";
import { TimelineView } from "../src/components/TimelineView";
import type { TimelineEvent } from "../src/lib/timeline";
let mounted: Root | undefined, container: HTMLDivElement | undefined;
const nativeFetch = globalThis.fetch;
afterEach(() => { act(() => mounted?.unmount()); container?.remove(); globalThis.fetch = nativeFetch; });
test("the visual sequence preserves backend ordering and keeps raw timestamps collapsed", async () => {
  const events: TimelineEvent[] = [
    { id: "late", description: "Later event", timestamp: "2019-01-31T11:00:00Z", status: "observed", evidence_ids: ["record-late"] },
    { id: "early", description: "Earlier event", timestamp: "2019-01-31T10:00:00Z", status: "reported", evidence_ids: ["record-early"] },
    { id: "unknown", description: "Uncertain event", timestamp: null, status: "inferred", evidence_ids: [] },
  ];
  globalThis.fetch = async () => Response.json({ dated: ["early", "late"].map(id => ({ event_id: id, instant: events.find(e => e.id === id)!.timestamp, display: events.find(e => e.id === id)!.timestamp, precision: "exact", timezone_known: true })), undated: [{ event_id: "unknown", instant: null, display: null, precision: "unknown", timezone_known: false }] });
  container = document.createElement("div"); document.body.append(container); mounted = createRoot(container);
  await act(async () => { mounted!.render(createElement(TimelineView, { events, caseId: "case", records: [] })); });
  assert.deepEqual([...container.querySelectorAll('[aria-labelledby="dated-events"] [data-event-id]')].map(el => el.getAttribute("data-event-id")), ["early", "late"]);
  assert.equal(container.querySelector('[aria-labelledby="uncertain-events"] [data-event-id]')?.getAttribute("data-event-id"), "unknown");
  for (const details of container.querySelectorAll<HTMLDetailsElement>(".blanc-event-details")) assert.equal(details.open, false);
  assert.ok(container.querySelector(".blanc-event-description")?.textContent?.includes("Earlier event"));
  assert.equal(container.querySelector(".dark\\:bg-zinc-950"), null);
});
