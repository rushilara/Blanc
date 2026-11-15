"use client";

import { createContext, useCallback, useContext, useMemo, type KeyboardEvent } from "react";
import {
  Background,
  BaseEdge,
  Controls,
  EdgeLabelRenderer,
  Handle,
  MiniMap,
  Position,
  ReactFlow,
  ReactFlowProvider,
  getBezierPath,
  useEdgesState,
  useNodesState,
  type Edge,
  type EdgeChange,
  type EdgeProps,
  type Node,
  type NodeChange,
  type NodeProps,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";

import { routeParallelEdge, withoutRemovals } from "@/lib/graphEdges";
import {
  FALLBACK_EDGE,
  STATUS_EDGE,
  buildElements,
  relationLabel,
  type EntityData,
  type GraphEdge,
  type GraphNode,
  type RelationData,
} from "@/lib/graphElements";

type Handlers = {
  onInspectNode: (nodeId: string) => void;
  onSelectEdge: (edgeId: string) => void;
};

const HandlersContext = createContext<Handlers | null>(null);

function EntityNode({ data, selected }: NodeProps<Node<EntityData>>) {
  return (
    <div
      className={`min-w-[9.5rem] max-w-[12rem] rounded-lg border bg-white px-3 py-2 shadow-sm dark:bg-zinc-950 ${
        selected
          ? "border-blue-500 ring-2 ring-blue-400/40"
          : data.pinned
            ? "border-amber-400 dark:border-amber-600"
            : "border-zinc-300 dark:border-zinc-700"
      }`}
    >
      <Handle type="target" position={Position.Left} isConnectable={false} className="!h-2 !w-2 !border-zinc-400 !bg-zinc-200 dark:!bg-zinc-700" />
      <div className="flex items-start justify-between gap-2">
        <p className="truncate text-sm font-medium text-zinc-900 dark:text-zinc-100">{data.label}</p>
        {data.pinned && (
          <span aria-label="Pinned" className="shrink-0 text-[10px] font-semibold uppercase tracking-wide text-amber-700 dark:text-amber-300">
            Pin
          </span>
        )}
      </div>
      <p className="truncate text-xs capitalize text-zinc-500">{data.entityType.replaceAll("_", " ")}</p>
      {!!data.evidenceIds.length && (
        <p className="mt-1 font-mono text-[10px] text-zinc-400">{data.evidenceIds.length} source{data.evidenceIds.length === 1 ? "" : "s"}</p>
      )}
      <Handle type="source" position={Position.Right} isConnectable={false} className="!h-2 !w-2 !border-zinc-400 !bg-zinc-200 dark:!bg-zinc-700" />
    </div>
  );
}

function RelationEdge({
  id,
  sourceX,
  sourceY,
  targetX,
  targetY,
  sourcePosition,
  targetPosition,
  data,
  markerEnd,
  selected,
  interactionWidth,
}: EdgeProps<Edge<RelationData>>) {
  const handlers = useContext(HandlersContext);
  const lane = data?.lane ?? { index: 0, count: 1, forward: true };
  // A lone edge keeps the default Bezier; parallel edges each get their own lane.
  const routed = lane.count > 1 ? routeParallelEdge({ sourceX, sourceY, targetX, targetY }, lane) : null;
  const [path, labelX, labelY] = routed
    ? [routed.path, routed.labelX, routed.labelY]
    : getBezierPath({ sourceX, sourceY, targetX, targetY, sourcePosition, targetPosition });
  const stroke = STATUS_EDGE[data?.status ?? ""] ?? FALLBACK_EDGE;
  const type = relationLabel(data?.relationType);
  const status = data?.status ?? "unknown";
  return (
    <>
      <BaseEdge
        id={id}
        path={path}
        markerEnd={markerEnd}
        interactionWidth={interactionWidth}
        style={{ stroke, strokeWidth: selected ? 2.5 : 1.75 }}
      />
      <EdgeLabelRenderer>
        <button
          type="button"
          data-edge-label={id}
          aria-haspopup={(data?.lane.count ?? 1) > 1 ? undefined : "dialog"}
          aria-label={data?.actionLabel ?? `Open sources for ${type}`}
          onClick={(event) => {
            event.stopPropagation();
            handlers?.onSelectEdge(id);
          }}
          className={`nodrag nopan pointer-events-auto absolute cursor-pointer rounded border px-1.5 py-0.5 text-[10px] font-medium shadow-sm focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-blue-600 ${
            selected
              ? "border-blue-400 bg-blue-50 text-blue-900 dark:border-blue-700 dark:bg-blue-950 dark:text-blue-100"
              : "border-zinc-200 bg-white text-zinc-700 hover:border-zinc-400 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-200"
          }`}
          style={{ transform: `translate(-50%, -50%) translate(${labelX}px, ${labelY}px)` }}
        >
          <span className="capitalize">{type}</span>
          <span className="text-zinc-500 dark:text-zinc-400"> · {status}</span>
        </button>
      </EdgeLabelRenderer>
    </>
  );
}

const nodeTypes = { entity: EntityNode };
const edgeTypes = { relation: RelationEdge };

function GraphInner({
  nodes,
  edges,
  pinnedIds,
  onInspectNode,
  onSelectEdge,
}: {
  nodes: GraphNode[];
  edges: GraphEdge[];
  pinnedIds: string[];
} & Handlers) {
  const built = useMemo(() => buildElements(nodes, edges, pinnedIds), [nodes, edges, pinnedIds]);
  // Initial state only: the parent remounts the graph for each revision.
  const [flowNodes, , applyNodeChanges] = useNodesState(built.flowNodes);
  const [flowEdges, , applyEdgeChanges] = useEdgesState(built.flowEdges);
  const handlers = useMemo(() => ({ onInspectNode, onSelectEdge }), [onInspectNode, onSelectEdge]);

  // Read-only view: drag, select and dimension changes apply; removals never do.
  const onNodesChange = useCallback(
    (changes: NodeChange<Node<EntityData>>[]) => applyNodeChanges(withoutRemovals(changes)),
    [applyNodeChanges],
  );
  const onEdgesChange = useCallback(
    (changes: EdgeChange<Edge<RelationData>>[]) => applyEdgeChanges(withoutRemovals(changes)),
    [applyEdgeChanges],
  );

  function onKeyDown(event: KeyboardEvent<HTMLDivElement>) {
    if (event.key !== "Enter") return;
    const target = event.target as HTMLElement;
    const nodeId = target.classList.contains("react-flow__node") ? target.dataset.id : undefined;
    if (nodeId) {
      event.preventDefault();
      onInspectNode(nodeId);
    }
  }

  return (
    <HandlersContext.Provider value={handlers}>
      <ReactFlow
        nodes={flowNodes}
        edges={flowEdges}
        onNodesChange={onNodesChange}
        onEdgesChange={onEdgesChange}
        nodeTypes={nodeTypes}
        edgeTypes={edgeTypes}
        deleteKeyCode={null}
        nodesConnectable={false}
        edgesReconnectable={false}
        elementsSelectable
        panOnScroll
        zoomOnScroll
        fitView
        colorMode="system"
        minZoom={0.4}
        maxZoom={1.75}
        proOptions={{ hideAttribution: true }}
        onNodeClick={(_, node) => onInspectNode(node.id)}
        onEdgeClick={(_, edge) => onSelectEdge(edge.id)}
        onKeyDown={onKeyDown}
        aria-label="Entity relationship graph"
        className="bg-zinc-50 dark:bg-zinc-900/40"
      >
        <Background gap={18} size={1} color="#a1a1aa" className="opacity-40" />
        <Controls showInteractive={false} className="!overflow-hidden !rounded-lg !border !border-zinc-200 !bg-white !shadow-none dark:!border-zinc-700 dark:!bg-zinc-950" />
        <MiniMap
          pannable
          zoomable
          className="!overflow-hidden !rounded-lg !border !border-zinc-200 !bg-white dark:!border-zinc-700 dark:!bg-zinc-950"
          nodeColor="#a1a1aa"
        />
      </ReactFlow>
    </HandlersContext.Provider>
  );
}

export default function RelationshipGraphCanvas({
  nodes,
  edges,
  pinnedIds = [],
  onInspectNode,
  onSelectEdge,
}: {
  nodes: GraphNode[];
  edges: GraphEdge[];
  pinnedIds?: string[];
} & Handlers) {
  if (!nodes.length && !edges.length) {
    return <p className="p-5 text-sm text-zinc-500">No entities to draw.</p>;
  }

  return (
    <ReactFlowProvider>
      <GraphInner
        nodes={nodes}
        edges={edges}
        pinnedIds={pinnedIds}
        onInspectNode={onInspectNode}
        onSelectEdge={onSelectEdge}
      />
    </ReactFlowProvider>
  );
}
