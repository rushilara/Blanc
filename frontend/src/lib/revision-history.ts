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

/** The open revision and its ancestors, oldest first. */
export function questionChain(revisions: RevisionSummary[], analysisId: string): RevisionSummary[] {
  const byId = new Map(revisions.map(item => [item.analysis_id, item]));
  const chain: RevisionSummary[] = [];
  const seen = new Set<string>();
  let current = byId.get(analysisId);
  while (current && !seen.has(current.analysis_id)) {
    seen.add(current.analysis_id);
    chain.push(current);
    current = current.parent_analysis_id ? byId.get(current.parent_analysis_id) : undefined;
  }
  return chain.reverse();
}

/** Original questions: revisions with no parent. Backend order is preserved. */
export function savedInvestigations(revisions: RevisionSummary[]): RevisionSummary[] {
  return revisions.filter(item => item.parent_analysis_id === null);
}

/** Every revision in the open investigation, oldest first, including later follow-ups. */
export function questionThread(revisions: RevisionSummary[], analysisId: string): RevisionSummary[] {
  const rootId = investigationRoot(revisions, analysisId);
  if (!rootId) return [];
  const children = new Map<string, RevisionSummary[]>();
  for (const item of [...revisions].reverse()) {
    if (!item.parent_analysis_id) continue;
    const list = children.get(item.parent_analysis_id) ?? [];
    list.push(item);
    children.set(item.parent_analysis_id, list);
  }
  const byId = new Map(revisions.map(item => [item.analysis_id, item]));
  const thread: RevisionSummary[] = [];
  const seen = new Set<string>();
  const walk = (id: string) => {
    if (seen.has(id)) return;
    const item = byId.get(id);
    if (!item) return;
    seen.add(id);
    thread.push(item);
    for (const child of children.get(id) ?? []) walk(child.analysis_id);
  };
  walk(rootId);
  return thread;
}
