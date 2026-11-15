import assert from "node:assert/strict";
import { test } from "node:test";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";

import type { AnalysisRevision } from "../src/lib/analysis";
import { RelationshipGraph } from "../src/components/RelationshipGraph";

type Graph = AnalysisRevision["graph"];

const render = (graph: Graph) =>
  renderToStaticMarkup(createElement(RelationshipGraph, { graph, caseId: "synthetic-demo", records: [] }));

const nodes: Graph["nodes"] = [
  { id: "alex", label: "Alex", type: "person", evidence_ids: ["rec-01"] },
  { id: "jordan", label: "Jordan", type: "person" },
];

test("a graph with entities but no relationships says so", () => {
  const html = render({ nodes, edges: [] });
  assert.match(html, /No supported relationships\./);
  assert.doesNotMatch(html, /aria-label="Relationships"/);
  assert.match(html, /Search entities and relationships/);
  assert.match(html, /Pin Alex/);
});

test("edges to unknown entities are listed with raw IDs and marked, not dropped", () => {
  const html = render({
    nodes,
    edges: [
      { id: "ok", source: "alex", target: "jordan", type: "emailed", status: "observed", evidence_ids: ["rec-01"] },
      { id: "orphan", source: "alex", target: "ghost-entity", type: "paid", status: "inferred", evidence_ids: ["rec-02"] },
    ],
  });
  assert.doesNotMatch(html, /No supported relationships/);
  assert.match(html, /data-edge-id="orphan"/);
  assert.match(html, /ghost-entity/);
  assert.equal(html.match(/data-unknown-entity/g)?.length, 1);
  const orphan = html.slice(html.indexOf('data-edge-id="orphan"'));
  assert.match(orphan, /References an entity not in this graph/);
});

test("relationship status uses the shared Status badge, like claims", () => {
  const html = render({
    nodes,
    edges: [{ id: "ok", source: "alex", target: "jordan", type: "emailed", status: "inferred", evidence_ids: ["rec-01"] }],
  });
  // The badge's distinctive classes for "inferred".
  assert.match(html, /rounded-full border[^"]*border-violet-300[^"]*">inferred<\/span>/);
});
