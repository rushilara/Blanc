import type { GraphEdge, GraphNode } from "@/lib/graphElements";

export type GraphFilterState = {
  query: string;
  nodeTypes: string[];
  edgeTypes: string[];
  pinnedIds: string[];
};

export type FilteredGraph = {
  nodes: GraphNode[];
  edges: GraphEdge[];
  active: boolean;
  matchedNodeIds: Set<string>;
  matchedEdgeIds: Set<string>;
};

function normalize(value: string | undefined): string {
  return (value ?? "").trim().toLowerCase();
}

function hasQuery(query: string): boolean {
  return normalize(query).length > 0;
}

function matchesQuery(haystack: string | undefined, query: string): boolean {
  const needle = normalize(query);
  return !needle || normalize(haystack).includes(needle);
}

/** Distinct sorted type values present in the graph (for filter controls). */
export function collectNodeTypes(nodes: GraphNode[]): string[] {
  return [...new Set(nodes.map((node) => node.type))].sort((a, b) => a.localeCompare(b));
}

export function collectEdgeTypes(edges: GraphEdge[]): string[] {
  return [...new Set(edges.map((edge) => edge.type || "related"))].sort((a, b) => a.localeCompare(b));
}

export function filtersActive(filters: Pick<GraphFilterState, "query" | "nodeTypes" | "edgeTypes">): boolean {
  return hasQuery(filters.query) || filters.nodeTypes.length > 0 || filters.edgeTypes.length > 0;
}

/**
 * Apply search and type filters. Pinned entities always remain visible.
 *
 * Node-type filters select entities without expanding through edges. Edge-type
 * filters and text search may pull in endpoints of matching relationships.
 * Edges are kept when both ends are visible and the edge passes any edge-type filter.
 */
export function filterGraph(nodes: GraphNode[], edges: GraphEdge[], filters: GraphFilterState): FilteredGraph {
  const active = filtersActive(filters);
  const pinned = new Set(filters.pinnedIds);
  const nodeTypeSet = new Set(filters.nodeTypes);
  const edgeTypeSet = new Set(filters.edgeTypes);
  const searching = hasQuery(filters.query);
  const byId = new Map(nodes.map((node) => [node.id, node]));
  const names = new Map(nodes.map((node) => [node.id, node.label]));

  const nodePassesType = (node: GraphNode | undefined) =>
    !!node && (nodeTypeSet.size === 0 || nodeTypeSet.has(node.type));
  const edgePassesType = (edge: GraphEdge) => edgeTypeSet.size === 0 || edgeTypeSet.has(edge.type || "related");
  const nodePassesSearch = (node: GraphNode) =>
    !searching ||
    matchesQuery(node.label, filters.query) ||
    matchesQuery(node.type, filters.query) ||
    matchesQuery(node.id, filters.query);
  const edgePassesSearch = (edge: GraphEdge) =>
    !searching ||
    matchesQuery(edge.type || "related", filters.query) ||
    matchesQuery(edge.description, filters.query) ||
    matchesQuery(names.get(edge.source), filters.query) ||
    matchesQuery(names.get(edge.target), filters.query) ||
    matchesQuery(edge.id, filters.query);

  // Node criteria only select entities when search or a node-type filter is on.
  // An edge-type-only filter should not treat every node as matched.
  const nodeCriteriaActive = searching || nodeTypeSet.size > 0;
  const matchedNodeIds = new Set<string>();
  if (nodeCriteriaActive) {
    for (const node of nodes) {
      if (nodePassesType(node) && nodePassesSearch(node)) matchedNodeIds.add(node.id);
    }
  }

  const matchedEdgeIds = new Set<string>();
  for (const edge of edges) {
    if (edgePassesType(edge) && edgePassesSearch(edge)) matchedEdgeIds.add(edge.id);
  }

  if (!active) {
    return { nodes, edges, active: false, matchedNodeIds, matchedEdgeIds };
  }

  const visibleIds = new Set<string>([...matchedNodeIds, ...pinned]);

  // Search or edge-type filters can introduce relationship endpoints.
  if (searching || edgeTypeSet.size > 0) {
    for (const edge of edges) {
      if (!matchedEdgeIds.has(edge.id)) continue;
      for (const end of [edge.source, edge.target]) {
        if (pinned.has(end) || nodePassesType(byId.get(end)) || searching || nodeTypeSet.size === 0) {
          visibleIds.add(end);
        }
      }
    }
  }

  const visibleNodes = nodes.filter((node) => visibleIds.has(node.id));
  const visibleEdges = edges.filter((edge) => {
    if (!visibleIds.has(edge.source) || !visibleIds.has(edge.target)) return false;
    return edgePassesType(edge);
  });

  return { nodes: visibleNodes, edges: visibleEdges, active: true, matchedNodeIds, matchedEdgeIds };
}
