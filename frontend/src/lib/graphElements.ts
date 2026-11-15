import { MarkerType, type Edge, type Node } from "@xyflow/react";

import type { AnalysisRevision } from "@/lib/analysis";
import { assignLanes, type Lane } from "@/lib/graphEdges";
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

/** Build read-only React Flow elements: draggable, selectable, never deletable or connectable. */
export function buildElements(nodes: GraphNode[], edges: GraphEdge[]) {
  const laidOut = layoutGraph(nodes, edges);
  const byId = new Map(laidOut.map((item) => [item.id, item]));
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
        evidenceIds: node.evidence_ids ?? [],
      },
      draggable: true,
      connectable: false,
      deletable: false,
      ariaLabel: `${node.label} (${node.type}). Press Enter to inspect cited sources.`,
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
      evidenceIds: edge.evidence_ids,
      lane: lanes.get(edge.id) ?? { index: 0, count: 1, forward: true },
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
