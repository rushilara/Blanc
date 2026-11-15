import assert from "node:assert/strict";
import { test } from "node:test";
import { timelineCaption } from "../src/lib/timeline-presentation";
import { revisionChange } from "../src/components/RevisionHistoryView";
import type { RevisionSummary } from "../src/lib/live-api";

test("exact normalized timestamps keep UTC and fractional seconds", () => {
  assert.deepEqual(timelineCaption({ event_id: "e", instant: "2019-01-31T21:04:31.629Z", precision: "exact", timezone_known: true, display: "2019-01-31T21:04:31.629Z" }), { date: "Jan 31, 2019", time: "21:04:31.629 UTC" });
});
test("day and month precision never acquire an invented clock time", () => {
  const day = timelineCaption({ event_id: "e", instant: "2019-01-31T00:00:00Z", precision: "day", timezone_known: false, display: "2019-01-31 (day)" });
  assert.equal(day.time, "Day precision");
  const month = timelineCaption({ event_id: "e", instant: "2019-01-01T00:00:00Z", precision: "month", timezone_known: false, display: "2019-01 (month)" });
  assert.deepEqual(month, { date: "Jan 2019", time: "Month precision" });
});
test("unknown zones and approximate times stay explicitly uncertain", () => {
  assert.equal(timelineCaption(null).date, "Time not established");
  for (const precision of ["exact", "approximate"] as const) {
    const caption = timelineCaption({ event_id: "e", instant: "2019-01-31T10:00:00Z", precision, timezone_known: false, display: "Source time; zone unknown" });
    assert.equal(caption.date, "Source time; zone unknown");
    assert.equal(caption.time, "Chronology uncertain");
  }
});
const root: RevisionSummary = { analysis_id: "root", parent_analysis_id: null, question: "Original question", created_at: "2026-01-01T00:00:00Z", excluded_record_ids: [], restored_from_analysis_id: null, needs_review: false };
test("revision stories distinguish questions and source changes from case events", () => {
  assert.equal(revisionChange(root).title, "Investigation opened");
  const child = { ...root, analysis_id: "child", parent_analysis_id: "root", question: "Follow-up?" };
  assert.equal(revisionChange(child, root).title, "Follow-up asked");
  const excluded = { ...child, question: root.question, excluded_record_ids: ["a"] };
  assert.equal(revisionChange(excluded, root).detail, "1 record excluded");
  assert.equal(revisionChange({ ...child, question: root.question }, excluded).detail, "1 record restored");
  assert.match(revisionChange(child).detail, /earlier version is not loaded/);
});
