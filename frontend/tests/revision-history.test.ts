import { afterEach, test } from "node:test";
import assert from "node:assert/strict";
import fixture from "../../fixtures/synthetic_analysis_revision.json";
import sourceFixture from "../../fixtures/synthetic_records.json";
import { ApiError, getRevision, listRevisions, loadRevisionSources, type RevisionSummary } from "../src/lib/live-api";
import { investigationRoot, questionChain, questionThread, savedInvestigations } from "../src/lib/revision-history";
import type { AnalysisRevision } from "../src/lib/analysis";
const nativeFetch = globalThis.fetch;
afterEach(() => { globalThis.fetch = nativeFetch; });
const root: RevisionSummary = { analysis_id: "root", parent_analysis_id: null, question: "Original?", created_at: "2026-11-14T12:00:00Z", needs_review: false, excluded_record_ids: [], restored_from_analysis_id: null };
const child = { ...root, analysis_id: "child", parent_analysis_id: "root", question: "Follow up?", excluded_record_ids: ["rec-02"] };
const sibling = { ...child, analysis_id: "sibling" };
const other = { ...root, analysis_id: "other" };

test("history keeps backend order and separates unrelated roots from sibling versions", async () => {
  const history = [child, sibling, other, root];
  const controller = new AbortController();
  globalThis.fetch = async (url, init) => {
    assert.ok(String(url).endsWith('/cases/case%2Fone/analyses'));
    assert.equal(init?.method, 'GET'); assert.equal(init?.signal, controller.signal);
    return Response.json(history);
  };
  assert.deepEqual(await listRevisions('case/one', controller.signal), history);
  assert.equal(investigationRoot(history, child.analysis_id), 'root');
  assert.equal(investigationRoot(history, sibling.analysis_id), 'root');
  assert.equal(investigationRoot(history, other.analysis_id), 'other');
  assert.equal(investigationRoot(history, 'missing'), null);
  assert.deepEqual(questionChain(history, child.analysis_id).map(item => item.analysis_id), ['root', 'child']);
  assert.deepEqual(questionThread(history, root.analysis_id).map(item => item.analysis_id), ['root', 'sibling', 'child']);
  assert.deepEqual(savedInvestigations(history).map(item => item.analysis_id), ['other', 'root']);
  assert.deepEqual(questionChain(history, 'missing'), []);
  assert.deepEqual(questionThread(history, 'missing'), []);
});

test("malformed history, missing parents, cycles and duplicate IDs are not displayed", async () => {
  for (const history of [null, [root, root], [child], [{ ...root, parent_analysis_id: 'root' }], [{ ...root, created_at: 'yesterday' }], [{ ...root, created_at: '2026-11-14T12:00:00' }], [{ ...root, needs_review: 'false' }], [{ ...root, excluded_record_ids: ['x', 'x'] }]]) {
    globalThis.fetch = async () => Response.json(history);
    await assert.rejects(listRevisions('case'), ApiError);
  }
  globalThis.fetch = async () => Response.json([]);
  assert.deepEqual(await listRevisions('empty'), []);
});

test("opening a revision validates case/ID/contract and uses GET without generating", async () => {
  const revision = structuredClone(fixture) as AnalysisRevision;
  revision.analysis_id = 'saved/one';
  globalThis.fetch = async (url, init) => {
    assert.ok(String(url).endsWith('/analyses/saved%2Fone')); assert.equal(init?.method, 'GET');
    return Response.json(revision);
  };
  assert.equal((await getRevision(revision.case_id, 'saved/one')).question, revision.question);
  await assert.rejects(getRevision('wrong-case', 'saved/one'), ApiError);
  globalThis.fetch = async () => Response.json({ ...revision, analysis_id: 'other' });
  await assert.rejects(getRevision(revision.case_id, 'saved/one'), ApiError);
  globalThis.fetch = async () => Response.json({ ...revision, claims: null });
  await assert.rejects(getRevision(revision.case_id, 'saved/one'), ApiError);
});

test("source hydration includes node-only and excluded originals, deduplicating and retaining partial success", async () => {
  const revision = structuredClone(fixture) as AnalysisRevision;
  revision.graph.nodes.push({ id: 'node-only', label: 'Node', type: 'person', evidence_ids: ['node-only-source'] });
  revision.excluded_record_ids = ['excluded-source'];
  const requested: string[] = [];
  globalThis.fetch = async (url, init) => {
    assert.equal(init?.method, 'GET');
    const record_id = decodeURIComponent(String(url).split('/').pop()!);
    requested.push(record_id);
    if (record_id === 'rec-02') return new Response('', { status: 404 });
    return Response.json({ record: { ...sourceFixture[0], record_id }, excerpt: null });
  };
  const sources = await loadRevisionSources(revision);
  assert.deepEqual(requested.sort(), ['excluded-source', 'node-only-source', 'rec-01', 'rec-02']);
  assert.deepEqual(sources.map(r => r.record_id).sort(), ['excluded-source', 'node-only-source', 'rec-01']);
});
