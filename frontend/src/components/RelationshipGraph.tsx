"use client";

import dynamic from "next/dynamic";
import { useCallback, useEffect, useId, useMemo, useRef, useState } from "react";

import type { AnalysisRevision, EvidenceRecord } from "@/lib/analysis";
import { SourceReferences } from "@/components/SourceReferences";
import { Status } from "@/components/StatusBadge";
import { resolveEdgeSelection } from "@/lib/graphEdges";
import {
  STATUS_EDGE,
  partitionEdges,
  relationLabel,
  sourceCountLabel,
  uniqueIds,
  type GraphEdge,
} from "@/lib/graphElements";

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
  excludedRecordIds,
  onExclude,
  onAnnotate,
}: {
  graph: Graph;
  caseId: string;
  records: EvidenceRecord[];
  excludedRecordIds?: string[];
  onExclude?: (recordId: string) => void;
  onAnnotate?: (recordId: string) => void;
}) {
  const [drawerIds, setDrawerIds] = useState<string[]>([]);
  const [openToken, setOpenToken] = useState(0);
  const [status, setStatus] = useState<string | null>(null);
  const [pair, setPair] = useState<GraphEdge[] | null>(null);
  const pairHeading = useRef<HTMLHeadingElement>(null);
  const pairTitleId = useId();
  const { drawable, dangling } = useMemo(() => partitionEdges(graph.nodes, graph.edges), [graph]);
  const danglingIds = new Set(dangling.map((edge) => edge.id));
  const names = new Map(graph.nodes.map((node) => [node.id, node.label]));
  const name = (id: string) => names.get(id) ?? id;
  const sourceProps = { records, caseId, excludedRecordIds, onExclude, onAnnotate };

  useEffect(() => {
    if (pair) pairHeading.current?.focus();
  }, [pair]);

  const inspect = useCallback((evidenceIds: string[], label: string) => {
    const ids = uniqueIds(evidenceIds);
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
      const selection = resolveEdgeSelection(drawable, edgeId);
      if (selection.kind === "sources") {
        setPair(null);
        inspect(selection.edge.evidence_ids, relationLabel(selection.edge.type));
      } else if (selection.kind === "pair") {
        setStatus(null);
        setPair(selection.edges);
      }
    },
    [drawable, inspect],
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
          edges={drawable}
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
        <section aria-labelledby={pairTitleId} className="space-y-2 rounded-lg border border-blue-200 p-3 dark:border-blue-900">
          <div className="flex items-start justify-between gap-3">
            <h3 id={pairTitleId} ref={pairHeading} tabIndex={-1} className="break-words text-sm font-semibold">
              {pair.length} relationships between {name(pair[0].source)} and {name(pair[0].target)}
            </h3>
            <button type="button" onClick={() => setPair(null)} className="rounded border border-zinc-300 px-2 py-1 text-xs dark:border-zinc-700">
              Close
            </button>
          </div>
          <ul className="space-y-2">
            {pair.map((edge) => (
              <li key={edge.id} data-pair-edge={edge.id} className="flex flex-wrap items-center justify-between gap-2 text-sm">
                <span className="flex flex-wrap items-center gap-2 break-words">
                  <span>
                    {name(edge.source)} <span aria-label="to">→</span> {name(edge.target)}: <span className="capitalize">{relationLabel(edge.type)}</span>
                  </span>
                  <Status value={edge.status} />
                </span>
                <button type="button" aria-haspopup="dialog" onClick={() => inspect(edge.evidence_ids, relationLabel(edge.type))}
                  className="rounded-md border border-blue-200 bg-blue-50 px-2 py-1 text-xs text-blue-800 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-blue-600 dark:border-blue-800 dark:bg-blue-950 dark:text-blue-200">
                  Open {sourceCountLabel(edge.evidence_ids)}
                </button>
              </li>
            ))}
          </ul>
        </section>
      )}
      {graph.edges.length > 0 ? (
        <ul aria-label="Relationships" className="space-y-3">
          {graph.edges.map((edge) => (
            <li key={edge.id} data-edge-id={edge.id} className="space-y-2 rounded-lg bg-zinc-50 p-3 dark:bg-zinc-900">
              <p className="break-words text-sm font-medium">
                {name(edge.source)} <span aria-label="to">→</span> {name(edge.target)}
              </p>
              {danglingIds.has(edge.id) && (
                <p data-unknown-entity className="text-xs text-amber-800 dark:text-amber-300">
                  References an entity not in this graph, so it is not drawn above.
                </p>
              )}
              <p className="text-xs capitalize text-zinc-500">{relationLabel(edge.type)}</p>
              {edge.description && <p className="break-words text-sm leading-6">{edge.description}</p>}
              <Status value={edge.status} />
              <SourceReferences {...sourceProps} ids={edge.evidence_ids} />
            </li>
          ))}
        </ul>
      ) : (
        <p className="text-sm text-zinc-500">No supported relationships.</p>
      )}
      {drawerIds.length > 0 && (
        <SourceReferences
          key={openToken}
          {...sourceProps}
          ids={drawerIds}
          autoOpen
          hideTriggers
        />
      )}
    </div>
  );
}
