import assert from "node:assert/strict";
import { test } from "node:test";
import { applyEdgeChanges, applyNodeChanges, type EdgeChange, type NodeChange } from "@xyflow/react";

import type { AnalysisRevision } from "../src/lib/analysis";
import {
  assignLanes,
  pairRelationships,
  resolveEdgeSelection,
  routeParallelEdge,
  withoutRemovals,
} from "../src/lib/graphEdges";
import { buildElements, edgeActionLabel, partitionEdges, sourceCountLabel } from "../src/lib/graphElements";

type Graph = AnalysisRevision["graph"];

const nodes: Graph["nodes"] = [
  { id: "alex", label: "Alex", type: "person", evidence_ids: ["rec-01"] },
  { id: "jordan", label: "Jordan", type: "person", evidence_ids: ["rec-02"] },
  { id: "casey", label: "Casey", type: "person", evidence_ids: ["rec-03"] },
];
const edges: Graph["edges"] = [
  { id: "edge-email", source: "alex", target: "jordan", type: "emailed", status: "observed", evidence_ids: ["rec-01"] },
  { id: "edge-paid", source: "alex", target: "jordan", type: "paid", status: "reported", evidence_ids: ["rec-02"] },
  { id: "edge-chat", source: "jordan", target: "casey", type: "chatted_with", status: "inferred", evidence_ids: ["rec-03"] },
];
// Alex's right handle to Jordan's left handle, as React Flow passes them to the edge.
const geometry = { sourceX: 200, sourceY: 60, targetX: 420, targetY: 60 };

test("parallel Alex→Jordan edges get distinct paths and label positions", () => {
  const lanes = assignLanes(edges);
  const email = routeParallelEdge(geometry, lanes.get("edge-email")!);
  const paid = routeParallelEdge(geometry, lanes.get("edge-paid")!);
  assert.notEqual(email.path, paid.path);
  assert.notDeepEqual([email.labelX, email.labelY], [paid.labelX, paid.labelY]);
  // Separated by at least a label's height, so neither label covers the other.
  assert.ok(Math.abs(email.labelY - paid.labelY) >= 30);
  assert.equal(lanes.get("edge-chat")!.count, 1);
});

test("a reverse-direction edge in the same pair takes its own lane", () => {
  const withReverse = [...edges, { ...edges[0], id: "edge-reply", source: "jordan", target: "alex" }];
  const lanes = assignLanes(withReverse);
  assert.equal(lanes.get("edge-reply")!.count, 3);
  const reverseGeometry = { sourceX: 420, sourceY: 60, targetX: 200, targetY: 60 };
  const routes = [
    routeParallelEdge(geometry, lanes.get("edge-email")!),
    routeParallelEdge(geometry, lanes.get("edge-paid")!),
    routeParallelEdge(reverseGeometry, lanes.get("edge-reply")!),
  ];
  assert.equal(new Set(routes.map((route) => route.path)).size, 3);
  assert.equal(new Set(routes.map((route) => `${route.labelX},${route.labelY}`)).size, 3);
});

test("selecting either parallel edge lists both relationships, each with its own sources", () => {
  for (const id of ["edge-email", "edge-paid"]) {
    const selection = resolveEdgeSelection(edges, id);
    assert.equal(selection.kind, "pair");
    if (selection.kind !== "pair") continue;
    assert.deepEqual(selection.edges.map((edge) => edge.id), ["edge-email", "edge-paid"]);
    assert.deepEqual(selection.edges.map((edge) => edge.evidence_ids), [["rec-01"], ["rec-02"]]);
  }
  const single = resolveEdgeSelection(edges, "edge-chat");
  assert.equal(single.kind, "sources");
  if (single.kind === "sources") assert.deepEqual(single.edge.evidence_ids, ["rec-03"]);
  assert.equal(resolveEdgeSelection(edges, "missing").kind, "none");
  assert.equal(pairRelationships(edges, "edge-chat").length, 1);
});

test("graph elements cannot be deleted: nondeletable, and remove changes are ignored", () => {
  const { flowNodes, flowEdges } = buildElements(nodes, edges);
  assert.ok(flowNodes.every((node) => node.deletable === false && node.draggable === true));
  assert.ok(flowEdges.every((edge) => edge.deletable === false));

  // What React Flow emits when Backspace/Delete hits a selected node and its edges.
  const nodeChanges: NodeChange[] = [
    { type: "remove", id: "alex" },
    { type: "position", id: "jordan", position: { x: 999, y: 5 } },
  ];
  const edgeChanges: EdgeChange[] = [
    { type: "remove", id: "edge-email" },
    { type: "remove", id: "edge-paid" },
  ];
  const nextNodes = applyNodeChanges(withoutRemovals(nodeChanges), flowNodes);
  const nextEdges = applyEdgeChanges(withoutRemovals(edgeChanges), flowEdges);
  assert.equal(nextNodes.length, flowNodes.length);
  assert.equal(nextEdges.length, flowEdges.length);
  // Dragging still works.
  assert.deepEqual(nextNodes.find((node) => node.id === "jordan")!.position, { x: 999, y: 5 });
  // Sanity: unfiltered, React Flow would have removed them.
  assert.equal(applyNodeChanges(nodeChanges, flowNodes).length, flowNodes.length - 1);
});

test("source counts use de-duplicated IDs, matching what the drawer shows", () => {
  const dupes = [{ ...edges[2], evidence_ids: ["rec-03", "rec-03", "rec-01", "rec-01"] }];
  assert.equal(sourceCountLabel(dupes[0].evidence_ids), "2 sources");
  assert.equal(sourceCountLabel(["rec-01", "rec-01"]), "1 source");
  const { flowNodes, flowEdges } = buildElements(
    [nodes[1], { ...nodes[2], evidence_ids: ["rec-03", "rec-03"] }],
    dupes,
  );
  assert.deepEqual(flowEdges[0].data!.evidenceIds, ["rec-03", "rec-01"]);
  assert.match(flowEdges[0].data!.actionLabel, /^Open 2 sources for /);
  assert.deepEqual(flowNodes[1].data.evidenceIds, ["rec-03"]);
  assert.match(flowNodes[1].ariaLabel!, /open 1 source\./);
});

test("an edge label's accessible name says what activating it does", () => {
  const name = (id: string) => nodes.find((node) => node.id === id)?.label ?? id;
  const choose = "Choose among 2 relationships between Alex and Jordan";
  const open = "Open 1 source for Jordan chatted with Casey (inferred)";
  assert.equal(edgeActionLabel(edges[0], edges, name), choose);
  assert.equal(edgeActionLabel(edges[2], edges, name), open);
  const { flowEdges } = buildElements(nodes, edges);
  assert.deepEqual(flowEdges.map((edge) => edge.data!.actionLabel), [choose, choose, open]);
});

test("edges referencing unknown entities are kept out of the canvas", () => {
  const orphan = { ...edges[2], id: "edge-orphan", target: "ghost" };
  const { drawable, dangling } = partitionEdges(nodes, [...edges, orphan]);
  assert.deepEqual(drawable.map((edge) => edge.id), ["edge-email", "edge-paid", "edge-chat"]);
  assert.deepEqual(dangling.map((edge) => edge.id), ["edge-orphan"]);
});
