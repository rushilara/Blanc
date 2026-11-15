import { test } from "node:test";
import assert from "node:assert/strict";
import {
  itemFor,
  sameFindingNote,
  sourcesInOrder,
  supportChanged,
  supportLabel,
  type ComparisonItem,
} from "../src/lib/comparison";

const items: ComparisonItem[] = [
  {
    kind: "claim",
    base_ref_id: "claim-1",
    compared_ref_id: null,
    support_status: "unresolved",
    base_evidence_ids: ["rec-01", "rec-02"],
    compared_evidence_ids: [],
    possible_matches: [{
      ref_id: "claim-x",
      shared_evidence_ids: ["rec-01"],
      remaining_sources: 1,
      original_sources: 2,
    }],
  },
  {
    kind: "claim",
    base_ref_id: null,
    compared_ref_id: "claim-x",
    support_status: "newly_supported",
    base_evidence_ids: [],
    compared_evidence_ids: ["rec-01"],
    possible_matches: [{
      ref_id: "claim-1",
      shared_evidence_ids: ["rec-01"],
      remaining_sources: 1,
      original_sources: 2,
    }],
  },
  {
    kind: "edge",
    base_ref_id: "edge-1",
    compared_ref_id: "edge-1",
    support_status: "still_supported",
    base_evidence_ids: ["rec-01"],
    compared_evidence_ids: ["rec-01"],
  },
];

test("looks up a finding on the side being shown", () => {
  assert.equal(itemFor(items, "claim", "claim-1", "base")?.support_status, "unresolved");
  assert.equal(itemFor(items, "claim", "claim-x", "compared")?.support_status, "newly_supported");
  assert.equal(itemFor(items, "claim", "claim-1", "compared"), undefined);
  assert.equal(itemFor(items, "edge", "edge-1", "compared")?.support_status, "still_supported");
});

test("excluded records stay listed after the citations, once each", () => {
  assert.deepEqual(sourcesInOrder(["rec-01", "rec-03"], ["rec-02", "rec-01"]), ["rec-01", "rec-03", "rec-02"]);
  assert.deepEqual(sourcesInOrder([], ["rec-02"]), ["rec-02"]);
});

test("a possible match is a note and does not change the status", () => {
  const base = itemFor(items, "claim", "claim-1", "base");
  assert.equal(base?.support_status, "unresolved");
  assert.equal(
    sameFindingNote(base?.possible_matches),
    "Possibly the same finding: 1 of 2 sources remain — please confirm",
  );
  assert.equal(sameFindingNote(undefined), null);
  assert.equal(supportLabel("newly_supported"), "Newly supported");
  assert.equal(supportChanged("still_supported"), false);
  assert.equal(supportChanged("weakened"), true);
});
