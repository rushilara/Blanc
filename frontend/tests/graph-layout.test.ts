import assert from "node:assert/strict";
import { test } from "node:test";
import { layoutGraph } from "../src/lib/graphLayout";

test("layoutGraph places a chain left-to-right without overlapping columns", () => {
  const positions = layoutGraph(
    [{ id: "alex" }, { id: "jordan" }, { id: "casey" }],
    [
      { source: "alex", target: "jordan" },
      { source: "jordan", target: "casey" },
    ],
  );
  const byId = Object.fromEntries(positions.map((item) => [item.id, item]));
  assert.ok(byId.alex.x < byId.jordan.x);
  assert.ok(byId.jordan.x < byId.casey.x);
  assert.equal(new Set(positions.map((item) => item.id)).size, 3);
});

test("layoutGraph keeps isolated nodes readable", () => {
  const positions = layoutGraph([{ id: "a" }, { id: "b" }], []);
  assert.equal(positions.length, 2);
  assert.notEqual(positions[0].y, positions[1].y);
});
