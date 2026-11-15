import { test } from "node:test";
import assert from "node:assert/strict";
import { groupTimeline, parseTimeline, type TimelineEvent, type NormalizedTimeline } from "../src/lib/timeline";

const events: TimelineEvent[] = ["late", "early", "day", "month", "approximate", "local", "missing"].map(id => ({
  id, description: id, timestamp: id === "missing" ? null : "source text", evidence_ids: ["r"], status: "reported",
}));
const entry = (event_id: string, precision: "exact" | "day" | "month" | "approximate" = "exact", timezone_known = true) => ({
  event_id, precision, timezone_known, instant: "2026-04-10T09:00:00Z", display: "normalized text",
});
const normalized: NormalizedTimeline = {
  dated: [entry("approximate", "approximate"), entry("month", "month", false), entry("day", "day", false), entry("local", "exact", false), entry("early"), entry("late")],
  undated: [{ event_id: "missing", instant: null, display: null, precision: "unknown", timezone_known: false }],
};

test("preserves backend order while separating uncertain times", () => {
  const groups = groupTimeline(events, parseTimeline(normalized, events));
  assert.deepEqual(groups.dated.map(i => i.event.id), ["month", "day", "early", "late"]);
  assert.deepEqual(groups.uncertain.map(i => i.event.id), ["approximate", "local", "missing"]);
  assert.equal(groups.dated[0].time?.display, "normalized text");
  assert.equal(events[0].id, "late"); // never mutates the source list
});

test("without normalization every source stays visible without claiming an order", () => {
  const groups = groupTimeline(events, null);
  assert.equal(groups.dated.length, 0);
  assert.equal(groups.uncertain.length, events.length);
  assert.ok(groups.uncertain.every(i => i.time === null));
});

test("rejects missing, duplicate, foreign and inconsistent normalization entries", () => {
  assert.throws(() => parseTimeline({ ...normalized, undated: [] }, events));
  assert.throws(() => parseTimeline({ ...normalized, dated: [...normalized.dated, entry("early")] }, events));
  assert.throws(() => parseTimeline({ ...normalized, dated: [entry("foreign"), ...normalized.dated.slice(1)] }, events));
  assert.throws(() => parseTimeline({ ...normalized, undated: [{ ...normalized.undated[0], instant: "2026-01-01T00:00:00Z" }] }, events));
});
