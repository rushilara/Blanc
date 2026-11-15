"use client";

import dynamic from "next/dynamic";
import { useState } from "react";

import type { AnalysisRevision, EvidenceRecord } from "@/lib/analysis";
import { SourceReferences } from "@/components/SourceReferences";

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

  function onInspect(evidenceIds: string[], label: string) {
    const ids = [...new Set(evidenceIds)];
    if (!ids.length) {
      setStatus(`No cited sources for ${label}.`);
      return;
    }
    setStatus(null);
    setDrawerIds(ids);
    setOpenToken((token) => token + 1);
  }

  return (
    <div className="space-y-3">
      <p className="text-xs text-zinc-500">
        Click a node or edge to open cited source evidence. Drag nodes to rearrange; pan and zoom to explore.
      </p>
      <div className="h-[min(28rem,55vh)] min-h-[18rem] overflow-hidden rounded-lg border border-zinc-200 dark:border-zinc-800">
        <RelationshipGraphCanvas
          nodes={graph.nodes}
          edges={graph.edges}
          onInspect={onInspect}
        />
      </div>
      {status && (
        <p role="status" className="text-xs text-amber-800 dark:text-amber-300">
          {status}
        </p>
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
