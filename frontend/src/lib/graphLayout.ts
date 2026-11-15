/** Deterministic layered layout for a typical analysis-sized graph (no editing). */

export type LayoutNode = { id: string };
export type LayoutEdge = { source: string; target: string };
export type LaidOutNode = { id: string; x: number; y: number };

const COL_GAP = 300;
const ROW_GAP = 110;
const ORIGIN_X = 40;
const ORIGIN_Y = 40;

/**
 * Column for each node: the length of the longest path reaching it.
 *
 * Cycles are broken deterministically by dropping back edges found with a DFS
 * that visits nodes and edges in input order; self-loops and edges to unknown
 * nodes are ignored. Longest-path layering then runs over a topological order of
 * the remaining DAG, so a node's column only ever settles after every
 * predecessor's has, and its descendants move with it.
 */
export function assignLayers(nodes: LayoutNode[], edges: LayoutEdge[]): Map<string, number> {
  const ids = nodes.map((node) => node.id);
  const idSet = new Set(ids);
  const outgoing = new Map<string, string[]>(ids.map((id) => [id, []]));
  for (const edge of edges) {
    if (!idSet.has(edge.source) || !idSet.has(edge.target) || edge.source === edge.target) continue;
    outgoing.get(edge.source)!.push(edge.target);
  }

  // Iterative DFS: mark back edges (to a node still on the stack) as removed.
  const state = new Map<string, "active" | "done">();
  const forward = new Map<string, string[]>(ids.map((id) => [id, []]));
  for (const root of ids) {
    if (state.has(root)) continue;
    state.set(root, "active");
    const stack: { id: string; next: number }[] = [{ id: root, next: 0 }];
    while (stack.length) {
      const frame = stack[stack.length - 1];
      const targets = outgoing.get(frame.id)!;
      if (frame.next >= targets.length) {
        state.set(frame.id, "done");
        stack.pop();
        continue;
      }
      const target = targets[frame.next++];
      const seen = state.get(target);
      if (seen === "active") continue; // back edge: closes a cycle
      forward.get(frame.id)!.push(target);
      if (!seen) {
        state.set(target, "active");
        stack.push({ id: target, next: 0 });
      }
    }
  }

  // Kahn's algorithm over the acyclic edges, seeded in input order.
  const indegree = new Map<string, number>(ids.map((id) => [id, 0]));
  for (const targets of forward.values()) {
    for (const target of targets) indegree.set(target, indegree.get(target)! + 1);
  }
  const layer = new Map<string, number>(ids.map((id) => [id, 0]));
  const queue = ids.filter((id) => indegree.get(id) === 0);
  for (let i = 0; i < queue.length; i++) {
    const id = queue[i];
    for (const target of forward.get(id)!) {
      layer.set(target, Math.max(layer.get(target)!, layer.get(id)! + 1));
      const remaining = indegree.get(target)! - 1;
      indegree.set(target, remaining);
      if (remaining === 0) queue.push(target);
    }
  }
  return layer;
}

export function layoutGraph(nodes: LayoutNode[], edges: LayoutEdge[]): LaidOutNode[] {
  if (!nodes.length) return [];
  const layers = assignLayers(nodes, edges);

  const columns = new Map<number, string[]>();
  for (const { id } of nodes) {
    const layer = layers.get(id) ?? 0;
    columns.set(layer, [...(columns.get(layer) ?? []), id]);
  }

  const positions: LaidOutNode[] = [];
  for (const [layer, column] of [...columns.entries()].sort((a, b) => a[0] - b[0])) {
    column.forEach((id, index) => {
      positions.push({
        id,
        x: ORIGIN_X + layer * COL_GAP,
        y: ORIGIN_Y + index * ROW_GAP,
      });
    });
  }
  return positions;
}
