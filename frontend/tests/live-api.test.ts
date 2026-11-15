import { afterEach, test } from "node:test";
import assert from "node:assert/strict";
import revision from "../../fixtures/synthetic_analysis_revision.json";
import records from "../../fixtures/synthetic_records.json";
import { analyzeCase, ApiError, getSource, listCases, parseRevision, searchCase } from "../src/lib/live-api";
import type { AnalysisRevision } from "../src/lib/analysis";

const originalFetch = globalThis.fetch;
afterEach(() => { globalThis.fetch = originalFetch; });
const reply = (body: unknown, status = 200) => {
  globalThis.fetch = async () => Response.json(body, { status });
};
const clone = () => structuredClone(revision) as AnalysisRevision;

test("renders only complete shared-contract analyses for the selected case/question", () => {
  assert.equal(parseRevision(revision, revision.case_id, revision.question).analysis_id, revision.analysis_id);
  assert.throws(() => parseRevision(revision, "another-case", revision.question), ApiError);
  assert.throws(() => parseRevision(revision, revision.case_id, "another question"), ApiError);
  assert.throws(() => parseRevision({ ...revision, claims: null }, revision.case_id, revision.question), ApiError);
  assert.throws(() => parseRevision({ ...revision, created_at: "bad date" }, revision.case_id, revision.question), ApiError);
});

test("accepts server retrieval coverage and rejects a malformed one", () => {
  const covered = clone();
  covered.retrieval_coverage = {
    evidence_window: "standard", max_records: 16, max_excerpt_chars: 32000,
    skipped: [{ record_id: "rec-x", rank: 17, reason: "record_limit", vector_rank: 10 }],
    budget_substituted: false, left_out_record_ids: ["rec-x"], offer_expanded_window: true,
  };
  assert.equal(parseRevision(covered, revision.case_id, revision.question).retrieval_coverage?.offer_expanded_window, true);
  const bad = structuredClone(covered) as unknown as { retrieval_coverage: { evidence_window: string } };
  bad.retrieval_coverage.evidence_window = "huge";
  assert.throws(() => parseRevision(bad, revision.case_id, revision.question), ApiError);
});

test("rejects excluded, unreturned and dangling graph references", () => {
  const excluded = clone(); excluded.excluded_record_ids = [excluded.claims[0].evidence_ids[0]];
  assert.throws(() => parseRevision(excluded, revision.case_id, revision.question), ApiError);
  const absent = clone(); absent.retrieved_record_ids = [];
  assert.throws(() => parseRevision(absent, revision.case_id, revision.question), ApiError);
  const dangling = clone(); dangling.graph.edges[0].target = "missing-node";
  assert.throws(() => parseRevision(dangling, revision.case_id, revision.question), ApiError);
});

test("encodes both source IDs and checks the returned case and record", async () => {
  const source = { ...records[0], case_id: "case/with space", record_id: "record/with space" };
  globalThis.fetch = async (input) => {
    assert.ok(String(input).endsWith("/cases/case%2Fwith%20space/records/record%2Fwith%20space"));
    return Response.json({ record: source, excerpt: null });
  };
  assert.equal((await getSource(source.case_id, source.record_id)).content, source.content);
  reply({ record: records[0] });
  await assert.rejects(getSource("wrong-case", records[0].record_id), ApiError);
  await assert.rejects(getSource(records[0].case_id, "wrong-record"), ApiError);
});

test("rejects malformed sources instead of displaying an unverified body", async () => {
  reply({ record: { ...records[0], content: 42 } });
  await assert.rejects(getSource(records[0].case_id, records[0].record_id), ApiError);
});

test("search preserves the submitted question and abort signal", async () => {
  const controller = new AbortController();
  globalThis.fetch = async (_input, init) => {
    assert.equal(init?.signal, controller.signal);
    assert.equal(init?.method, "POST");
    assert.deepEqual(JSON.parse(String(init?.body)), { question: "a question" });
    return Response.json({ case_id: "case-a", question: "a question", records: [], dropped_by_limit: 0, dropped_by_budget: 0 });
  };
  assert.deepEqual((await searchCase("case-a", "a question", controller.signal)).records, []);
});

test("rejects wrong-case and malformed excerpt search responses", async () => {
  const body = { case_id: "case-a", question: "question", records: [], dropped_by_limit: 0, dropped_by_budget: 0 };
  reply(body); await assert.rejects(searchCase("case-b", "question"), ApiError);
  reply({ ...body, records: [{ record_id: "r", source_file: "a", source_locator: "line 1", excerpts: [{ text: "x", char_start: 4, char_end: 1 }] }] });
  await assert.rejects(searchCase("case-a", "question"), ApiError);
});

test("missing analysis endpoint gives an actionable message without exposing server text", async () => {
  reply({ detail: "internal provider information" }, 404);
  await assert.rejects(analyzeCase("case-a", "question"), (error: unknown) => {
    assert.ok(error instanceof ApiError);
    assert.match(error.message, /not available/);
    assert.doesNotMatch(error.message, /internal provider/);
    return true;
  });
});

test("case-list corruption and invalid JSON produce errors, not empty success states", async () => {
  reply([{ case_id: "case-a", title: null }]);
  await assert.rejects(listCases(), ApiError);
  globalThis.fetch = async () => new Response("not json");
  await assert.rejects(listCases(), /invalid JSON/);
});
