"use client";

import { useEffect, useRef } from "react";
import type { AnalysisRevision, EvidenceRecord } from "@/lib/analysis";

/** The approved spatial renderer, isolated from the reading view's typography. */
export function SpatialConnections({ graph, excludedRecordIds, records, onOpenSource }: {
  graph: AnalysisRevision["graph"];
  excludedRecordIds: string[];
  records: EvidenceRecord[];
  onOpenSource: (id: string) => void;
}) {
  const frame = useRef<HTMLIFrameElement>(null);
  function sendGraph() {
    frame.current?.contentWindow?.postMessage({ type: "blanc:graph", graph, excludedRecordIds, records }, window.location.origin);
  }
  useEffect(() => {
    frame.current?.contentWindow?.postMessage({ type: "blanc:graph", graph, excludedRecordIds, records }, window.location.origin);
    const allowed = new Set([...graph.nodes.flatMap(node => node.evidence_ids ?? []), ...graph.edges.flatMap(edge => edge.evidence_ids)]);
    const receive = (event: MessageEvent) => {
      if (event.origin !== window.location.origin || event.source !== frame.current?.contentWindow) return;
      if (event.data?.type === "blanc:open-source" && typeof event.data.recordId === "string" && allowed.has(event.data.recordId) && !excludedRecordIds.includes(event.data.recordId)) {
        onOpenSource(event.data.recordId);
      }
    };
    window.addEventListener("message", receive);
    return () => window.removeEventListener("message", receive);
  }, [graph, excludedRecordIds, records, onOpenSource]);

  if (!graph.nodes.length) return <p>No entities were identified in this analysis.</p>;
  return <section aria-label="Spatial connections">
    <iframe ref={frame} title="Interactive 3D case connections" src="/blanc/connections.html" onLoad={sendGraph} className="blanc-spatial-frame" />
    <details className="blanc-spatial-list">
      <summary>Entities and relationships · accessible list</summary>
      {graph.nodes.map(node => <div key={node.id}>
        <strong>{node.label}</strong> · {node.type}
        {[...new Set(node.evidence_ids ?? [])].filter(id => !excludedRecordIds.includes(id)).map(id => <button type="button" key={id} onClick={() => onOpenSource(id)}>Open source {id}</button>)}
      </div>)}
      {graph.edges.map(edge => <div key={edge.id}>
        <p>{graph.nodes.find(node => node.id === edge.source)?.label ?? edge.source} → {graph.nodes.find(node => node.id === edge.target)?.label ?? edge.target}: {edge.type.replaceAll("_", " ")} · {edge.status}</p>
        {edge.description && <p>{edge.description}</p>}
        {[...new Set(edge.evidence_ids)].filter(id => !excludedRecordIds.includes(id)).map(id => <button type="button" key={id} onClick={() => onOpenSource(id)}>Open source {id}</button>)}
      </div>)}
    </details>
  </section>;
}
