/** Deterministic layered layout for a typical analysis-sized graph (no editing). */

export type LayoutNode = { id: string };
export type LayoutEdge = { source: string; target: string };
export type LaidOutNode = { id: string; x: number; y: number };

const COL_GAP = 300;
const ROW_GAP = 110;
const ORIGIN_X = 40;
const ORIGIN_Y = 40;

export function layoutGraph(nodes: LayoutNode[], edges: LayoutEdge[]): LaidOutNode[] {
  if (!nodes.length) return [];

  const ids = nodes.map((node) => node.id);
  const idSet = new Set(ids);
  const incoming = new Map<string, number>(ids.map((id) => [id, 0]));
  const outgoing = new Map<string, string[]>(ids.map((id) => [id, []]));

  for (const edge of edges) {
    if (!idSet.has(edge.source) || !idSet.has(edge.target) || edge.source === edge.target) continue;
    incoming.set(edge.target, (incoming.get(edge.target) ?? 0) + 1);
    outgoing.get(edge.source)?.push(edge.target);
  }

  const layerOf = new Map<string, number>();
  const queue = ids.filter((id) => (incoming.get(id) ?? 0) === 0);
  if (!queue.length) queue.push(ids[0]);

  for (const id of queue) layerOf.set(id, 0);

  for (let i = 0; i < queue.length; i++) {
    const id = queue[i];
    const layer = layerOf.get(id) ?? 0;
    for (const next of outgoing.get(id) ?? []) {
      const proposed = layer + 1;
      if (!layerOf.has(next) || (layerOf.get(next) ?? 0) < proposed) {
        layerOf.set(next, proposed);
      }
      if (!queue.includes(next)) queue.push(next);
    }
  }

  for (const id of ids) {
    if (!layerOf.has(id)) layerOf.set(id, 0);
  }

  const columns = new Map<number, string[]>();
  for (const id of ids) {
    const layer = layerOf.get(id) ?? 0;
    const column = columns.get(layer) ?? [];
    column.push(id);
    columns.set(layer, column);
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
