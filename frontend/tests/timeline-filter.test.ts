import assert from "node:assert/strict";
import { test } from "node:test";

import type { TimelineEvent } from "../src/lib/timeline";
import { filterTimeline } from "../src/lib/timelineFilter";

const events: TimelineEvent[] = [
  {
    id: "event-1",
    description: "Alex wrote to Jordan",
    timestamp: "2026-04-10T09:15:00Z",
    timestamp_kind: "source_stated",
    evidence_ids: ["rec-01"],
    status: "reported",
  },
  {
    id: "event-2",
    description: "Possible meeting discussed",
    timestamp: null,
    timestamp_kind: "inferred",
    evidence_ids: ["rec-02"],
    status: "inferred",
  },
];

test("event type filter keeps matching kinds", () => {
  const result = filterTimeline(events, { query: "", eventTypes: ["inferred"] });
  assert.deepEqual(
    result.events.map((event) => event.id),
    ["event-2"],
  );
  assert.equal(result.active, true);
});

test("search matches description text", () => {
  const result = filterTimeline(events, { query: "jordan", eventTypes: [] });
  assert.deepEqual(
    result.events.map((event) => event.id),
    ["event-1"],
  );
});

test("empty filters leave every event", () => {
  const result = filterTimeline(events, { query: "", eventTypes: [] });
  assert.equal(result.active, false);
  assert.equal(result.events.length, 2);
});
