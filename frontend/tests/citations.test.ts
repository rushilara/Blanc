import assert from "node:assert/strict";
import fs from "node:fs";
import { afterEach, test } from "node:test";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";

import type { AnalysisRevision, EvidenceRecord } from "../src/lib/analysis";
import { citedRecordIds } from "../src/lib/citations";
import { loadRevisionSources } from "../src/lib/live-api";
import { SourceReferences } from "../src/components/SourceReferences";

const fixture = JSON.parse(fs.readFileSync(new URL("../../fixtures/synthetic_analysis_revision.json", import.meta.url), "utf8")) as AnalysisRevision;
const [baseRecord] = JSON.parse(fs.readFileSync(new URL("../../fixtures/synthetic_records.json", import.meta.url), "utf8")) as EvidenceRecord[];
const nativeFetch = globalThis.fetch;
afterEach(() => { globalThis.fetch = nativeFetch; });

const record = (id: string): EvidenceRecord => ({
  ...baseRecord,
  record_id: id,
  source_file: `${id}.txt`,
  content: `Synthetic content of ${id}`,
});

// rec-03 is cited only by a graph node: no claim, edge or timeline event cites it.
function nodeOnlyRevision(): AnalysisRevision {
  const revision = structuredClone(fixture);
  revision.retrieved_record_ids = [...revision.retrieved_record_ids, "rec-03"];
  revision.graph.nodes.push({ id: "dana", label: "Dana", type: "person", evidence_ids: ["rec-03"] });
  return revision;
}

test("collects citations from claims, nodes, edges and events without duplicates", () => {
  const revision = nodeOnlyRevision();
  revision.graph.nodes.push({ id: "eve", label: "Eve", type: "person" }); // no evidence_ids
  assert.deepEqual(citedRecordIds(revision), ["rec-01", "rec-02", "rec-03"]);
  assert.deepEqual(citedRecordIds({ ...revision, graph: { ...revision.graph, nodes: [] } }), ["rec-01", "rec-02"]);
});

test("a node-only citation is requested and its drawer shows the original", async () => {
  const requested: string[] = [];
  globalThis.fetch = async (url) => {
    const id = decodeURIComponent(String(url).split("/").pop()!);
    requested.push(id);
    return Response.json({ record: record(id), excerpt: null });
  };
  const loaded = await loadRevisionSources(nodeOnlyRevision());
  assert.ok(requested.includes("rec-03"));
  assert.ok(loaded.some((item) => item.record_id === "rec-03"));

  // The same drawer the graph opens for the Dana node.
  const html = renderToStaticMarkup(
    createElement(SourceReferences, { ids: ["rec-03"], caseId: fixture.case_id, records: loaded, autoOpen: true, hideTriggers: true }),
  );
  assert.match(html, /Synthetic content of rec-03/);
  assert.match(html, /rec-03\.txt/);
  assert.doesNotMatch(html, /source is unavailable/);
});

test("a failed lookup is left out, never replaced with other text", async () => {
  globalThis.fetch = async (url) => {
    const id = decodeURIComponent(String(url).split("/").pop()!);
    return id === "rec-03" ? new Response("", { status: 404 }) : Response.json({ record: record(id), excerpt: null });
  };
  const loaded = await loadRevisionSources(nodeOnlyRevision());
  assert.deepEqual(loaded.map((item) => item.record_id).sort(), ["rec-01", "rec-02"]);
  // The drawer then fetches it itself (BLA-40) rather than showing anything unverified.
  const html = renderToStaticMarkup(
    createElement(SourceReferences, { ids: ["rec-03"], caseId: fixture.case_id, records: loaded, autoOpen: true, hideTriggers: true }),
  );
  assert.doesNotMatch(html, /Synthetic content of rec-03/);
  assert.match(html, /Loading source record/);
});

test("hideTriggers renders only the drawer, with no citation chips", () => {
  const props = { ids: ["rec-01"], caseId: fixture.case_id, records: [record("rec-01")], autoOpen: true };
  const hidden = renderToStaticMarkup(createElement(SourceReferences, { ...props, hideTriggers: true }));
  const shown = renderToStaticMarkup(createElement(SourceReferences, props));
  assert.doesNotMatch(hidden, /Source record references/);
  assert.match(shown, /Source record references/);
  assert.match(hidden, /Synthetic content of rec-01/);
});
