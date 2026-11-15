"use client";

import { useEffect, useMemo } from "react";
import {
  Background,
  Controls,
  MarkerType,
  MiniMap,
  ReactFlow,
  ReactFlowProvider,
  useEdgesState,
  useNodesState,
  useReactFlow,
  type Edge,
  type Node,
  type NodeProps,
  Handle,
  Position,
  BaseEdge,
  EdgeLabelRenderer,
  getBezierPath,
  type EdgeProps,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";

import type { AnalysisRevision } from "@/lib/analysis";
import { layoutGraph } from "@/lib/graphLayout";

type GraphNode = AnalysisRevision["graph"]["nodes"][number];
type GraphEdge = AnalysisRevision["graph"]["edges"][number];

type EntityData = {
  label: string;
  entityType: string;
  evidenceIds: string[];
};

type RelationData = {
  relationType: string;
  status: string;
  description?: string;
  evidenceIds: string[];
};

const STATUS_EDGE: Record<string, string> = {
  observed: "#059669",
  reported: "#2563eb",
  inferred: "#7c3aed",
  unresolved: "#d97706",
};

function EntityNode({ data, selected }: NodeProps<Node<EntityData>>) {
  return (
    <div
      className={`min-w-[9.5rem] max-w-[12rem] rounded-lg border bg-white px-3 py-2 shadow-sm dark:bg-zinc-950 ${
        selected
          ? "border-blue-500 ring-2 ring-blue-400/40"
          : "border-zinc-300 dark:border-zinc-700"
      }`}
    >
      <Handle type="target" position={Position.Left} className="!h-2 !w-2 !border-zinc-400 !bg-zinc-200 dark:!bg-zinc-700" />
      <p className="truncate text-sm font-medium text-zinc-900 dark:text-zinc-100">{data.label}</p>
      <p className="truncate text-xs capitalize text-zinc-500">{data.entityType.replaceAll("_", " ")}</p>
      {!!data.evidenceIds.length && (
        <p className="mt-1 font-mono text-[10px] text-zinc-400">{data.evidenceIds.length} source{data.evidenceIds.length === 1 ? "" : "s"}</p>
      )}
      <Handle type="source" position={Position.Right} className="!h-2 !w-2 !border-zinc-400 !bg-zinc-200 dark:!bg-zinc-700" />
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
}: EdgeProps<Edge<RelationData>>) {
  const [path, labelX, labelY] = getBezierPath({
    sourceX,
    sourceY,
    targetX,
    targetY,
    sourcePosition,
    targetPosition,
  });
  const stroke = STATUS_EDGE[data?.status ?? ""] ?? "#71717a";
  return (
    <>
      <BaseEdge
        id={id}
        path={path}
        markerEnd={markerEnd}
        style={{ stroke, strokeWidth: selected ? 2.5 : 1.75 }}
      />
      <EdgeLabelRenderer>
        <div
          className={`nodrag nopan pointer-events-none absolute rounded border px-1.5 py-0.5 text-[10px] font-medium capitalize shadow-sm ${
            selected
              ? "border-blue-400 bg-blue-50 text-blue-900 dark:border-blue-700 dark:bg-blue-950 dark:text-blue-100"
              : "border-zinc-200 bg-white text-zinc-700 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-200"
          }`}
          style={{ transform: `translate(-50%, -50%) translate(${labelX}px, ${labelY}px)` }}
        >
          {(data?.relationType ?? "related").replaceAll("_", " ")}
        </div>
      </EdgeLabelRenderer>
    </>
  );
}

const nodeTypes = { entity: EntityNode };
const edgeTypes = { relation: RelationEdge };

function buildElements(nodes: GraphNode[], edges: GraphEdge[]) {
  const laidOut = layoutGraph(nodes, edges);
  const byId = new Map(laidOut.map((item) => [item.id, item]));
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
    },
    markerEnd: { type: MarkerType.ArrowClosed, width: 16, height: 16, color: STATUS_EDGE[edge.status] ?? "#71717a" },
    interactionWidth: 24,
  }));
  return { flowNodes, flowEdges };
}

function FitViewOnChange({ nodeCount, edgeCount }: { nodeCount: number; edgeCount: number }) {
  const { fitView } = useReactFlow();
  useEffect(() => {
    const timer = window.setTimeout(() => fitView({ padding: 0.2, duration: 200 }), 30);
    return () => window.clearTimeout(timer);
  }, [fitView, nodeCount, edgeCount]);
  return null;
}

function GraphInner({
  nodes,
  edges,
  onInspect,
}: {
  nodes: GraphNode[];
  edges: GraphEdge[];
  onInspect: (evidenceIds: string[], label: string) => void;
}) {
  const built = useMemo(() => buildElements(nodes, edges), [nodes, edges]);
  const [flowNodes, setNodes, onNodesChange] = useNodesState(built.flowNodes);
  const [flowEdges, setEdges, onEdgesChange] = useEdgesState(built.flowEdges);

  useEffect(() => {
    setNodes(built.flowNodes);
    setEdges(built.flowEdges);
  }, [built, setNodes, setEdges]);

  return (
    <ReactFlow
      nodes={flowNodes}
      edges={flowEdges}
      onNodesChange={onNodesChange}
      onEdgesChange={onEdgesChange}
      nodeTypes={nodeTypes}
      edgeTypes={edgeTypes}
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
      onNodeClick={(_, node) => {
        const data = node.data as EntityData;
        onInspect(data.evidenceIds, data.label);
      }}
      onEdgeClick={(_, edge) => {
        const data = edge.data as RelationData | undefined;
        const label = (data?.relationType ?? "relationship").replaceAll("_", " ");
        onInspect(data?.evidenceIds ?? [], label);
      }}
      aria-label="Entity relationship graph"
      className="bg-zinc-50 dark:bg-zinc-900/40"
    >
      <FitViewOnChange nodeCount={nodes.length} edgeCount={edges.length} />
      <Background gap={18} size={1} color="#a1a1aa" className="opacity-40" />
      <Controls showInteractive={false} className="!overflow-hidden !rounded-lg !border !border-zinc-200 !bg-white !shadow-none dark:!border-zinc-700 dark:!bg-zinc-950" />
      <MiniMap
        pannable
        zoomable
        className="!overflow-hidden !rounded-lg !border !border-zinc-200 !bg-white dark:!border-zinc-700 dark:!bg-zinc-950"
        nodeColor="#a1a1aa"
      />
    </ReactFlow>
  );
}

export default function RelationshipGraphCanvas({
  nodes,
  edges,
  onInspect,
}: {
  nodes: GraphNode[];
  edges: GraphEdge[];
  onInspect: (evidenceIds: string[], label: string) => void;
}) {
  if (!nodes.length && !edges.length) {
    return <p className="p-5 text-sm text-zinc-500">No supported relationships.</p>;
  }

  return (
    <ReactFlowProvider>
      <GraphInner nodes={nodes} edges={edges} onInspect={onInspect} />
    </ReactFlowProvider>
  );
}

