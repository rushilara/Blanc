import assert from "node:assert/strict";
import { test } from "node:test";
import { assignLayers, layoutGraph } from "../src/lib/graphLayout";

const ids = (...names: string[]) => names.map((id) => ({ id }));
const edge = (source: string, target: string) => ({ source, target });
const layers = (nodes: { id: string }[], edges: { source: string; target: string }[]) =>
  Object.fromEntries(assignLayers(nodes, edges));

test("layoutGraph places a chain left-to-right without overlapping columns", () => {
  const positions = layoutGraph(ids("alex", "jordan", "casey"), [edge("alex", "jordan"), edge("jordan", "casey")]);
  const byId = Object.fromEntries(positions.map((item) => [item.id, item]));
  assert.ok(byId.alex.x < byId.jordan.x);
  assert.ok(byId.jordan.x < byId.casey.x);
  assert.equal(new Set(positions.map((item) => item.id)).size, 3);
});

test("layoutGraph keeps isolated nodes readable", () => {
  const positions = layoutGraph(ids("a", "b"), []);
  assert.equal(positions.length, 2);
  assert.notEqual(positions[0].y, positions[1].y);
});

test("a node whose column rises after it is first reached moves its descendants too", () => {
  // A→B, A→C, C→B, B→D: B must sit after C, and D after B.
  assert.deepEqual(layers(ids("A", "B", "C", "D"), [edge("A", "B"), edge("A", "C"), edge("C", "B"), edge("B", "D")]),
    { A: 0, C: 1, B: 2, D: 3 });
  // Same graph with edges listed in a different order gives the same layers.
  assert.deepEqual(layers(ids("A", "B", "C", "D"), [edge("B", "D"), edge("C", "B"), edge("A", "C"), edge("A", "B")]),
    { A: 0, C: 1, B: 2, D: 3 });
});

test("cycles terminate with a stable layout by dropping the back edge", () => {
  const nodes = ids("A", "B", "C", "D");
  const edges = [edge("A", "B"), edge("B", "C"), edge("C", "A"), edge("C", "D")];
  const first = layers(nodes, edges);
  assert.deepEqual(first, { A: 0, B: 1, C: 2, D: 3 });
  assert.deepEqual(layers(nodes, edges), first);
  // A two-node cycle, and a cycle with no entry point, still place every node.
  assert.deepEqual(layers(ids("x", "y"), [edge("x", "y"), edge("y", "x")]), { x: 0, y: 1 });
  assert.equal(layoutGraph(nodes, edges).length, 4);
});

test("disconnected components, self-loops and unknown endpoints are handled", () => {
  const result = layers(ids("a", "b", "c", "d", "lonely"), [
    edge("a", "b"), edge("c", "d"), edge("d", "d"), edge("a", "a"), edge("b", "ghost"), edge("ghost", "c"),
  ]);
  assert.deepEqual(result, { a: 0, b: 1, c: 0, d: 1, lonely: 0 });
  const positions = layoutGraph(ids("a", "b", "c", "d", "lonely"), [edge("a", "b"), edge("c", "d")]);
  const spots = new Set(positions.map((p) => `${p.x},${p.y}`));
  assert.equal(spots.size, 5);
});
