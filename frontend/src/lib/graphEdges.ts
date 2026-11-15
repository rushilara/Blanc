/** Pure helpers for read-only relationship graph edges (no React Flow runtime). */

export type EdgeEnds = { id: string; source: string; target: string };

/** Position of one edge among all edges joining the same unordered node pair. */
export type Lane = {
  index: number;
  count: number;
  /** True when the edge runs from the pair's first ID (sorted) to its second. */
  forward: boolean;
};

export type EdgeGeometry = { sourceX: number; sourceY: number; targetX: number; targetY: number };
export type RoutedEdge = { path: string; labelX: number; labelY: number };

/** Distance between neighbouring parallel edges, measured at their midpoints. */
export const PARALLEL_SPACING = 40;

export function pairKey(source: string, target: string): string {
  return source <= target ? JSON.stringify([source, target]) : JSON.stringify([target, source]);
}

/** Assign each edge a lane within its node pair, counting both directions together. */
export function assignLanes(edges: EdgeEnds[]): Map<string, Lane> {
  const groups = new Map<string, EdgeEnds[]>();
  for (const edge of edges) {
    const key = pairKey(edge.source, edge.target);
    groups.set(key, [...(groups.get(key) ?? []), edge]);
  }
  const lanes = new Map<string, Lane>();
  for (const group of groups.values()) {
    group.forEach((edge, index) => {
      lanes.set(edge.id, { index, count: group.length, forward: edge.source <= edge.target });
    });
  }
  return lanes;
}

/** Signed offset of a lane from the straight line between the pair. */
export function laneOffset({ index, count }: Pick<Lane, "index" | "count">): number {
  return (index - (count - 1) / 2) * PARALLEL_SPACING;
}

/**
 * Route one edge of a parallel group as a quadratic curve bowed away from the
 * straight line by its lane offset. The offset is measured along the normal of
 * the pair's canonical direction, so reverse-direction edges in the same group
 * land on distinct sides instead of mirroring onto each other.
 */
export function routeParallelEdge(geometry: EdgeGeometry, lane: Lane): RoutedEdge {
  const { sourceX, sourceY, targetX, targetY } = geometry;
  const midX = (sourceX + targetX) / 2;
  const midY = (sourceY + targetY) / 2;
  const dx = lane.forward ? targetX - sourceX : sourceX - targetX;
  const dy = lane.forward ? targetY - sourceY : sourceY - targetY;
  const length = Math.hypot(dx, dy);
  const [normalX, normalY] = length < 1 ? [0, -1] : [-dy / length, dx / length];
  const offset = laneOffset(lane);
  const labelX = midX + normalX * offset;
  const labelY = midY + normalY * offset;
  // A quadratic curve passes through (start + 2*control + end) / 4 at t = 0.5,
  // so doubling the offset for the control point puts the curve's midpoint on the label.
  const controlX = midX + normalX * offset * 2;
  const controlY = midY + normalY * offset * 2;
  const round = (value: number) => Math.round(value * 100) / 100;
  return {
    path: `M ${round(sourceX)},${round(sourceY)} Q ${round(controlX)},${round(controlY)} ${round(targetX)},${round(targetY)}`,
    labelX: round(labelX),
    labelY: round(labelY),
  };
}

/** Every edge joining the same two nodes as `edgeId` (either direction), in input order. */
export function pairRelationships<E extends EdgeEnds>(edges: E[], edgeId: string): E[] {
  const edge = edges.find((item) => item.id === edgeId);
  if (!edge) return [];
  const key = pairKey(edge.source, edge.target);
  return edges.filter((item) => pairKey(item.source, item.target) === key);
}

export type EdgeSelection<E> =
  | { kind: "sources"; edge: E }
  | { kind: "pair"; edges: E[] }
  | { kind: "none" };

/**
 * What activating an edge (path or label) opens: its own sources when it is the
 * only relationship between its nodes, otherwise a chooser listing every
 * relationship between that pair so none depends on precise pointer targeting.
 */
export function resolveEdgeSelection<E extends EdgeEnds>(edges: E[], edgeId: string): EdgeSelection<E> {
  const pair = pairRelationships(edges, edgeId);
  if (!pair.length) return { kind: "none" };
  if (pair.length === 1) return { kind: "sources", edge: pair[0] };
  return { kind: "pair", edges: pair };
}

/** Drop element removals: the graph is an exploration view and nothing may delete evidence. */
export function withoutRemovals<C extends { type: string }>(changes: C[]): C[] {
  return changes.filter((change) => change.type !== "remove");
}
