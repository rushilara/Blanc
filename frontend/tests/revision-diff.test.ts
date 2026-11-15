import assert from "node:assert/strict";
import test from "node:test";
import base from "../../fixtures/synthetic_analysis_revision.json";
import compared from "../../fixtures/synthetic_analysis_revision_excluded.json";
import fixture from "../../fixtures/synthetic_revision_comparison.json";
import type { AnalysisRevision } from "../src/lib/analysis";
import type { RevisionComparison } from "../src/lib/comparison";
import { changeCounts, diffRows, evidenceState, orderPair } from "../src/lib/revision-diff";

const a = base as AnalysisRevision, b = compared as AnalysisRevision;
const comparison = fixture as RevisionComparison;

test("each side shows what it analyzed and what it excluded", () => {
  assert.deepEqual(evidenceState(a), { included: ["rec-01", "rec-02"], excluded: [] });
  assert.deepEqual(evidenceState(b), { included: ["rec-01"], excluded: ["rec-02"] });
});

test("the older revision is always the base", () => {
  assert.deepEqual(orderPair(b, a).map(r => r.analysis_id), [a.analysis_id, b.analysis_id]);
  assert.deepEqual(orderPair(a, b).map(r => r.analysis_id), [a.analysis_id, b.analysis_id]);
});

test("removed and changed findings come first, with the server's status labels", () => {
  const rows = diffRows(a, b, comparison);
  assert.deepEqual(changeCounts(rows), { added: 0, removed: 3, changed: 0, unchanged: 3 });
  assert.deepEqual(rows.slice(0, 3).map(r => [r.kind, r.change, r.statusLabel, r.right]), [
    ["claim", "removed", "Not supported", null],
    ["edge", "removed", "Not supported", null],
    ["event", "removed", "Not supported", null],
  ]);
  assert.ok(rows[0].left && rows[0].leftEvidence.includes("rec-02"));
  assert.deepEqual(rows.slice(3).map(r => r.change), ["unchanged", "unchanged", "unchanged"]);
});

test("added, changed and possible-match rows keep the cautious wording", () => {
  const items = structuredClone(comparison.items);
  items[0] = { ...items[0], support_status: "weakened", compared_evidence_ids: ["rec-01"] };
  items[1] = { ...items[1], possible_matches: [{ ref_id: "claim-9", shared_evidence_ids: ["rec-01"], remaining_sources: 1, original_sources: 2 }] };
  items.push({ kind: "claim", base_ref_id: null, compared_ref_id: "claim-1", support_status: "newly_supported", base_evidence_ids: [], compared_evidence_ids: ["rec-01"] });
  const rows = diffRows(a, b, { ...comparison, items });
  const changed = rows.find(r => r.change === "changed");
  assert.equal(changed?.statusLabel, "Weakened");
  const removed = rows.find(r => r.change === "removed" && r.kind === "claim");
  assert.equal(removed?.note, "Possibly the same finding: 1 of 2 sources remain — please confirm");
  assert.equal(removed?.statusLabel, "Not supported", "the note never changes the status");
  assert.equal(rows.find(r => r.change === "added")?.statusLabel, "Newly supported");
});

test("a comparison for another pair is refused", () => {
  assert.throws(() => diffRows(b, a, comparison), /different pair/);
});
