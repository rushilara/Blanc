import type { RevisionSummary } from "./live-api";

// Called on validated, complete, case-scoped history. Sibling branches belong to
// the same investigation even when their questions differ.
export function investigationRoot(revisions: RevisionSummary[], analysisId: string): string | null {
  const byId = new Map(revisions.map(r => [r.analysis_id, r]));
  const seen = new Set<string>();
  let current = byId.get(analysisId);
  while (current && !seen.has(current.analysis_id)) {
    seen.add(current.analysis_id);
    if (current.parent_analysis_id === null) return current.analysis_id;
    current = byId.get(current.parent_analysis_id);
  }
  return null;
}
