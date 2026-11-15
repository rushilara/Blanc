import { test } from "node:test";
import assert from "node:assert/strict";
import { earlierTurns, turnLabel } from "../src/lib/thread";
import type { AnalysisRevision } from "../src/lib/analysis";

const rev = (id: string, question: string) => ({ analysis_id: id, question }) as AnalysisRevision;

test("each earlier question appears once, as its latest revision", () => {
  const ancestors = [rev("a1", "What did they say?"), rev("a2", "What did they say?"), rev("b1", "Who received it?")];
  assert.deepEqual(earlierTurns(ancestors, rev("c1", "Which account is Steve?")).map(r => r.analysis_id), ["a2", "b1"]);
});

test("re-runs of the current question are not shown as earlier turns", () => {
  const ancestors = [rev("a1", "Q one"), rev("b1", "Q two"), rev("b2", " q  TWO ")];
  assert.deepEqual(earlierTurns(ancestors, rev("b3", "Q two")).map(r => r.analysis_id), ["a1"]);
});

test("a first question with no follow-ups has no thread", () => {
  assert.deepEqual(earlierTurns([], rev("a1", "Q")), []);
  assert.deepEqual(earlierTurns([rev("a1", "Q")], rev("a2", "Q")), []);
});

test("a question asked again later is a new turn", () => {
  const ancestors = [rev("a1", "Q one"), rev("b1", "Q two"), rev("a3", "Q one")];
  assert.deepEqual(earlierTurns(ancestors, rev("c1", "Q three")).map(r => r.analysis_id), ["a1", "b1", "a3"]);
});

test("turn labels", () => {
  assert.equal(turnLabel(0), "Question");
  assert.equal(turnLabel(2), "Follow-up 2");
});
