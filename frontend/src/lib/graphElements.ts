import { MarkerType, type Edge, type Node } from "@xyflow/react";

import type { AnalysisRevision } from "@/lib/analysis";
import { assignLanes, pairRelationships, type Lane } from "@/lib/graphEdges";
import { layoutGraph } from "@/lib/graphLayout";

export type GraphNode = AnalysisRevision["graph"]["nodes"][number];
export type GraphEdge = AnalysisRevision["graph"]["edges"][number];

export type EntityData = {
  label: string;
  entityType: string;
  evidenceIds: string[];
};

export type RelationData = {
  relationType: string;
  status: string;
  description?: string;
  evidenceIds: string[];
  lane: Lane;
  /** Accessible name for the edge label: what activating it actually does. */
  actionLabel: string;
};

export const STATUS_EDGE: Record<string, string> = {
  observed: "#059669",
  reported: "#2563eb",
  inferred: "#7c3aed",
  unresolved: "#d97706",
};
export const FALLBACK_EDGE = "#71717a";

export function relationLabel(type: string | undefined): string {
  return (type || "related").replaceAll("_", " ");
}

/** Cited IDs without duplicates, in citation order; the drawer shows exactly these. */
export function uniqueIds(ids: string[] | undefined): string[] {
  return [...new Set(ids ?? [])];
}

export function sourceCountLabel(ids: string[] | undefined): string {
  const count = uniqueIds(ids).length;
  return `${count} source${count === 1 ? "" : "s"}`;
}

/**
 * Split edges into those the canvas can draw and those referencing a node that
 * isn't in the graph (React Flow would silently drop those, error 008).
 */
export function partitionEdges(nodes: GraphNode[], edges: GraphEdge[]) {
  const known = new Set(nodes.map((node) => node.id));
  const drawable: GraphEdge[] = [];
  const dangling: GraphEdge[] = [];
  for (const edge of edges) {
    (known.has(edge.source) && known.has(edge.target) ? drawable : dangling).push(edge);
  }
  return { drawable, dangling };
}

/** What activating an edge's label does, phrased for its accessible name. */
export function edgeActionLabel(edge: GraphEdge, edges: GraphEdge[], name: (id: string) => string): string {
  const pair = pairRelationships(edges, edge.id);
  if (pair.length > 1) {
    return `Choose among ${pair.length} relationships between ${name(edge.source)} and ${name(edge.target)}`;
  }
  return `Open ${sourceCountLabel(edge.evidence_ids)} for ${name(edge.source)} ${relationLabel(edge.type)} ${name(edge.target)} (${edge.status})`;
}

/**
 * Build read-only React Flow elements: draggable, selectable, never deletable or
 * connectable. Pass only drawable edges (see partitionEdges).
 */
export function buildElements(nodes: GraphNode[], edges: GraphEdge[]) {
  const laidOut = layoutGraph(nodes, edges);
  const byId = new Map(laidOut.map((item) => [item.id, item]));
  const names = new Map(nodes.map((node) => [node.id, node.label]));
  const name = (id: string) => names.get(id) ?? id;
  const lanes = assignLanes(edges);
  const flowNodes: Node<EntityData>[] = nodes.map((node) => {
    const spot = byId.get(node.id) ?? { x: 0, y: 0 };
    return {
      id: node.id,
      type: "entity",
      position: { x: spot.x, y: spot.y },
      data: {
        label: node.label,
        entityType: node.type,
        evidenceIds: uniqueIds(node.evidence_ids),
      },
      draggable: true,
      connectable: false,
      deletable: false,
      ariaLabel: `${node.label} (${node.type}). Press Enter to open ${sourceCountLabel(node.evidence_ids)}.`,
    };
  });
  const flowEdges: Edge<RelationData>[] = edges.map((edge) => ({
    id: edge.id,
    source: edge.source,
    target: edge.target,
    type: "relation",
    data: {
      relationType: edge.type,
      status: edge.status,
      description: edge.description,
      evidenceIds: uniqueIds(edge.evidence_ids),
      lane: lanes.get(edge.id) ?? { index: 0, count: 1, forward: true },
      actionLabel: edgeActionLabel(edge, edges, name),
    },
    markerEnd: { type: MarkerType.ArrowClosed, width: 16, height: 16, color: STATUS_EDGE[edge.status] ?? FALLBACK_EDGE },
    interactionWidth: 18,
    deletable: false,
    reconnectable: false,
    // The edge's label button is its keyboard target; avoid a second, silent tab stop.
    focusable: false,
  }));
  return { flowNodes, flowEdges };
}
