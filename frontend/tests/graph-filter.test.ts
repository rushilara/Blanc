import assert from "node:assert/strict";
import { test } from "node:test";

import type { GraphEdge, GraphNode } from "../src/lib/graphElements";
import { filterGraph } from "../src/lib/graphFilter";

const nodes: GraphNode[] = [
  { id: "alex", label: "Alex", type: "person", evidence_ids: ["rec-01"] },
  { id: "jordan", label: "Jordan", type: "person", evidence_ids: ["rec-01"] },
  { id: "server", label: "Mail host", type: "device", evidence_ids: ["rec-02"] },
];

const edges: GraphEdge[] = [
  {
    id: "edge-1",
    source: "alex",
    target: "jordan",
    type: "communicated_with",
    status: "reported",
    evidence_ids: ["rec-01"],
  },
  {
    id: "edge-2",
    source: "jordan",
    target: "server",
    type: "accessed",
    status: "observed",
    evidence_ids: ["rec-02"],
  },
];

test("node type filter keeps pinned entities visible", () => {
  const result = filterGraph(nodes, edges, {
    query: "",
    nodeTypes: ["device"],
    edgeTypes: [],
    pinnedIds: ["alex"],
  });
  assert.deepEqual(
    result.nodes.map((node) => node.id).sort(),
    ["alex", "server"],
  );
  assert.ok(result.active);
});

test("search matches edge text and includes endpoints", () => {
  const result = filterGraph(nodes, edges, {
    query: "accessed",
    nodeTypes: [],
    edgeTypes: [],
    pinnedIds: [],
  });
  assert.deepEqual(
    result.nodes.map((node) => node.id).sort(),
    ["jordan", "server"],
  );
  assert.deepEqual(
    result.edges.map((edge) => edge.id),
    ["edge-2"],
  );
});

test("edge type filter hides non-matching relationships", () => {
  const result = filterGraph(nodes, edges, {
    query: "",
    nodeTypes: [],
    edgeTypes: ["communicated_with"],
    pinnedIds: [],
  });
  assert.deepEqual(
    result.edges.map((edge) => edge.id),
    ["edge-1"],
  );
  assert.deepEqual(
    result.nodes.map((node) => node.id).sort(),
    ["alex", "jordan"],
  );
});

test("edge type filter keeps both endpoints of matching relationships", () => {
  const result = filterGraph(nodes, edges, {
    query: "",
    nodeTypes: [],
    edgeTypes: ["accessed"],
    pinnedIds: [],
  });
  assert.deepEqual(
    result.edges.map((edge) => edge.id),
    ["edge-2"],
  );
  assert.deepEqual(
    result.nodes.map((node) => node.id).sort(),
    ["jordan", "server"],
  );
});

test("search does not override an active node-type filter", () => {
  const result = filterGraph(nodes, edges, {
    query: "accessed",
    nodeTypes: ["device"],
    edgeTypes: [],
    pinnedIds: [],
  });
  assert.deepEqual(
    result.nodes.map((node) => node.id),
    ["server"],
  );
  assert.deepEqual(result.edges.map((edge) => edge.id), []);
});

test("empty filters leave the full graph", () => {
  const result = filterGraph(nodes, edges, {
    query: "  ",
    nodeTypes: [],
    edgeTypes: [],
    pinnedIds: ["alex"],
  });
  assert.equal(result.active, false);
  assert.equal(result.nodes.length, 3);
  assert.equal(result.edges.length, 2);
});
