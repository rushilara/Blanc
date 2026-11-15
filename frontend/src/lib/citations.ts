import type { AnalysisRevision, EvidenceRecord } from "@/lib/analysis";

type CitingParts = Pick<AnalysisRevision, "claims" | "graph" | "timeline_events">;

/**
 * Every source record ID a revision cites, in first-seen order: claims, graph
 * nodes, graph edges and timeline events. Anything that can open the evidence
 * drawer must be covered here, or its original is never loaded.
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

/**
 * Fetch the original of every cited record. Failed lookups are dropped, so the
 * drawer shows them as unavailable rather than substituting model text.
 */
export async function loadCitedSources(
  revision: CitingParts,
  fetchSource: (id: string) => Promise<EvidenceRecord>,
): Promise<EvidenceRecord[]> {
  const settled = await Promise.allSettled(citedRecordIds(revision).map((id) => fetchSource(id)));
  return settled.flatMap((result) => (result.status === "fulfilled" ? [result.value] : []));
}
