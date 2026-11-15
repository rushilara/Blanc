import assert from "node:assert/strict";
import { test } from "node:test";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";

import type { AnalysisRevision, EvidenceRecord } from "../src/lib/analysis";
import { citedRecordIds, loadCitedSources } from "../src/lib/citations";
import { SourceReferences } from "../src/components/SourceReferences";

const record = (id: string): EvidenceRecord => ({
  record_id: id,
  case_id: "synthetic-demo",
  source_file: `${id}.txt`,
  source_locator: "line 1",
  content: `Synthetic content of ${id}`,
});
const store = new Map(["rec-01", "rec-02", "rec-03"].map((id) => [id, record(id)]));

// rec-03 is cited only by a graph node: no claim, edge or timeline event cites it.
const revision: Pick<AnalysisRevision, "claims" | "graph" | "timeline_events"> = {
  claims: [{ id: "c1", text: "Claim", status: "reported", evidence_ids: ["rec-01"] }],
  graph: {
    nodes: [
      { id: "alex", label: "Alex", type: "person", evidence_ids: ["rec-01"] },
      { id: "casey", label: "Casey", type: "person", evidence_ids: ["rec-03"] },
      { id: "jordan", label: "Jordan", type: "person" },
    ],
    edges: [{ id: "e1", source: "alex", target: "jordan", type: "emailed", status: "observed", evidence_ids: ["rec-02"] }],
  },
  timeline_events: [{ id: "t1", description: "Event", timestamp: null, status: "reported", evidence_ids: ["rec-02"] }],
};

test("collects citations from claims, nodes, edges and events without duplicates", () => {
  assert.deepEqual(citedRecordIds(revision), ["rec-01", "rec-03", "rec-02"]);
});

test("a node-only citation is requested and its drawer shows the original", async () => {
  const requested: string[] = [];
  const loaded = await loadCitedSources(revision, async (id) => {
    requested.push(id);
    const found = store.get(id);
    if (!found) throw new Error("not found");
    return found;
  });
  assert.ok(requested.includes("rec-03"));
  assert.ok(loaded.some((item) => item.record_id === "rec-03"));

  // The same drawer the graph opens for the Casey node.
  const html = renderToStaticMarkup(
    createElement(SourceReferences, { ids: ["rec-03"], caseId: "synthetic-demo", records: loaded, autoOpen: true, hideTriggers: true }),
  );
  assert.match(html, /Synthetic content of rec-03/);
  assert.match(html, /rec-03\.txt/);
  assert.doesNotMatch(html, /source is unavailable/);
});

test("a failed lookup is left out, never replaced with other text", async () => {
  const loaded = await loadCitedSources(revision, async (id) => {
    if (id === "rec-03") throw new Error("not found");
    return store.get(id)!;
  });
  assert.deepEqual(loaded.map((item) => item.record_id).sort(), ["rec-01", "rec-02"]);
  // The drawer then fetches it itself (BLA-40) rather than showing anything unverified.
  const html = renderToStaticMarkup(
    createElement(SourceReferences, { ids: ["rec-03"], caseId: "synthetic-demo", records: loaded, autoOpen: true, hideTriggers: true }),
  );
  assert.doesNotMatch(html, /Synthetic content of rec-03/);
  assert.match(html, /Loading source record/);
});

test("hideTriggers renders only the drawer, with no citation chips", () => {
  const props = { ids: ["rec-01"], caseId: "synthetic-demo", records: [record("rec-01")], autoOpen: true };
  const hidden = renderToStaticMarkup(createElement(SourceReferences, { ...props, hideTriggers: true }));
  const shown = renderToStaticMarkup(createElement(SourceReferences, props));
  assert.doesNotMatch(hidden, /Source record references/);
  assert.match(shown, /Source record references/);
  assert.match(hidden, /Synthetic content of rec-01/);
});
