import type { AnalysisRevision } from "@/lib/analysis";

type CitingParts = Pick<AnalysisRevision, "claims" | "graph" | "timeline_events">;

/**
 * Every source record ID a revision cites, in first-seen order: claims, graph
 * nodes, graph edges and timeline events. Anything that can open the evidence
 * drawer must be covered here, or its original is never loaded
 * (see loadRevisionSources in live-api.ts).
 */
export function citedRecordIds(revision: CitingParts): string[] {
  const findings: { evidence_ids?: string[] }[] = [
    ...revision.claims,
    ...revision.graph.nodes,
    ...revision.graph.edges,
    ...revision.timeline_events,
  ];
  return [...new Set(findings.flatMap((finding) => finding.evidence_ids ?? []))];
}
