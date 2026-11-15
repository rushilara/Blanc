"use client";

import dynamic from "next/dynamic";
import { useCallback, useEffect, useRef, useState } from "react";

import type { AnalysisRevision, EvidenceRecord } from "@/lib/analysis";
import { SourceReferences } from "@/components/SourceReferences";
import { resolveEdgeSelection } from "@/lib/graphEdges";
import { STATUS_EDGE, relationLabel, type GraphEdge } from "@/lib/graphElements";

const RelationshipGraphCanvas = dynamic(() => import("./RelationshipGraphCanvas"), {
  ssr: false,
  loading: () => (
    <p role="status" className="p-5 text-sm text-zinc-500">
      Loading relationship graph…
    </p>
  ),
});

type Graph = AnalysisRevision["graph"];

export function RelationshipGraph({
  graph,
  caseId,
  records,
}: {
  graph: Graph;
  caseId: string;
  records: EvidenceRecord[];
}) {
  const [drawerIds, setDrawerIds] = useState<string[]>([]);
  const [openToken, setOpenToken] = useState(0);
  const [status, setStatus] = useState<string | null>(null);
  const [pair, setPair] = useState<GraphEdge[] | null>(null);
  const pairHeading = useRef<HTMLHeadingElement>(null);
  const names = new Map(graph.nodes.map((node) => [node.id, node.label]));
  const name = (id: string) => names.get(id) ?? id;

  useEffect(() => {
    if (pair) pairHeading.current?.focus();
  }, [pair]);

  const inspect = useCallback((evidenceIds: string[], label: string) => {
    const ids = [...new Set(evidenceIds)];
    if (!ids.length) {
      setStatus(`No cited sources for ${label}.`);
      return;
    }
    setStatus(null);
    setDrawerIds(ids);
    setOpenToken((token) => token + 1);
  }, []);

  const onInspectNode = useCallback(
    (nodeId: string) => {
      const node = graph.nodes.find((item) => item.id === nodeId);
      if (node) inspect(node.evidence_ids ?? [], node.label);
    },
    [graph.nodes, inspect],
  );

  const onSelectEdge = useCallback(
    (edgeId: string) => {
      const selection = resolveEdgeSelection(graph.edges, edgeId);
      if (selection.kind === "sources") {
        setPair(null);
        inspect(selection.edge.evidence_ids, relationLabel(selection.edge.type));
      } else if (selection.kind === "pair") {
        setStatus(null);
        setPair(selection.edges);
      }
    },
    [graph.edges, inspect],
  );

  return (
    <div className="space-y-3">
      <p className="text-xs text-zinc-500">
        Click a node, edge or edge label (or focus it and press Enter) to open cited source evidence. Drag nodes to rearrange; pan and zoom to explore.
      </p>
      <ul aria-label="Relationship status legend" className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-zinc-600 dark:text-zinc-300">
        {Object.entries(STATUS_EDGE).map(([value, color]) => (
          <li key={value} className="flex items-center gap-1.5 capitalize">
            <span aria-hidden="true" className="inline-block h-0.5 w-4" style={{ backgroundColor: color }} />
            {value}
          </li>
        ))}
      </ul>
      <div className="h-[min(28rem,55vh)] min-h-[18rem] overflow-hidden rounded-lg border border-zinc-200 dark:border-zinc-800">
        <RelationshipGraphCanvas
          nodes={graph.nodes}
          edges={graph.edges}
          onInspectNode={onInspectNode}
          onSelectEdge={onSelectEdge}
        />
      </div>
      {status && (
        <p role="status" className="text-xs text-amber-800 dark:text-amber-300">
          {status}
        </p>
      )}
      {pair && (
        <section aria-labelledby="relationship-pair-title" className="space-y-2 rounded-lg border border-blue-200 p-3 dark:border-blue-900">
          <div className="flex items-start justify-between gap-3">
            <h3 id="relationship-pair-title" ref={pairHeading} tabIndex={-1} className="break-words text-sm font-semibold">
              {pair.length} relationships between {name(pair[0].source)} and {name(pair[0].target)}
            </h3>
            <button type="button" onClick={() => setPair(null)} className="rounded border border-zinc-300 px-2 py-1 text-xs dark:border-zinc-700">
              Close
            </button>
          </div>
          <ul className="space-y-2">
            {pair.map((edge) => (
              <li key={edge.id} data-pair-edge={edge.id} className="flex flex-wrap items-center justify-between gap-2 text-sm">
                <span className="break-words">
                  {name(edge.source)} <span aria-label="to">→</span> {name(edge.target)}: <span className="capitalize">{relationLabel(edge.type)}</span> ({edge.status})
                </span>
                <button type="button" aria-haspopup="dialog" onClick={() => inspect(edge.evidence_ids, relationLabel(edge.type))}
                  className="rounded-md border border-blue-200 bg-blue-50 px-2 py-1 text-xs text-blue-800 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-blue-600 dark:border-blue-800 dark:bg-blue-950 dark:text-blue-200">
                  Open {edge.evidence_ids.length} source{edge.evidence_ids.length === 1 ? "" : "s"}
                </button>
              </li>
            ))}
          </ul>
        </section>
      )}
      {graph.edges.length > 0 && (
        <ul aria-label="Relationships" className="space-y-3">
          {graph.edges.map((edge) => (
            <li key={edge.id} data-edge-id={edge.id} className="space-y-1.5 rounded-lg bg-zinc-50 p-3 dark:bg-zinc-900">
              <p className="break-words text-sm font-medium">
                {name(edge.source)} <span aria-label="to">→</span> {name(edge.target)}
              </p>
              <p className="text-xs text-zinc-500">
                <span className="capitalize">{relationLabel(edge.type)}</span> · Status: <span className="font-medium">{edge.status}</span>
              </p>
              {edge.description && <p className="break-words text-sm leading-6">{edge.description}</p>}
              <SourceReferences records={records} caseId={caseId} ids={edge.evidence_ids} />
            </li>
          ))}
        </ul>
      )}
      {drawerIds.length > 0 && (
        <SourceReferences
          key={openToken}
          records={records}
          caseId={caseId}
          ids={drawerIds}
          autoOpen
          hideTriggers
        />
      )}
    </div>
  );
}
