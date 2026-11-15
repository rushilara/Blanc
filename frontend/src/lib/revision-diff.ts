// Side-by-side diff of any two revisions of one question (BLA-50). The server's
// comparison (BLA-34) decides each finding's support status; this only lays it out in
// two columns and labels what was added, removed or changed. Status labels and the
// "possibly the same finding" note (BLA-63) are shown as the server sent them.
import type { AnalysisRevision } from "./analysis";
import { sameFindingNote, supportLabel, type ComparisonItem, type ComparisonKind, type RevisionComparison, type SupportStatus } from "./comparison";

export type Change = "added" | "removed" | "changed" | "unchanged";

export type DiffRow = {
  kind: ComparisonKind;
  change: Change;
  status: SupportStatus;
  statusLabel: string;
  /** Wording on each side; null where the finding does not exist on that side. */
  left: string | null;
  right: string | null;
  leftEvidence: string[];
  rightEvidence: string[];
  /** Advisory BLA-63 wording, or null. Never changes the status. */
  note: string | null;
  explanation?: string;
};

export type EvidenceState = { included: string[]; excluded: string[] };

/** What a revision analyzed (retrieved and not excluded) and what it excluded. */
export function evidenceState(revision: AnalysisRevision): EvidenceState {
  const excluded = new Set(revision.excluded_record_ids);
  return {
    included: revision.retrieved_record_ids.filter(id => !excluded.has(id)),
    excluded: [...revision.excluded_record_ids],
  };
}

/** The older revision is the base, so "added" always means added later. */
export function orderPair(a: AnalysisRevision, b: AnalysisRevision): [AnalysisRevision, AnalysisRevision] {
  const ta = Date.parse(a.created_at), tb = Date.parse(b.created_at);
  if (ta !== tb) return ta < tb ? [a, b] : [b, a];
  return a.analysis_id < b.analysis_id ? [a, b] : [b, a];
}

export function findingText(revision: AnalysisRevision, kind: ComparisonKind, id: string | null): string | null {
  if (!id) return null;
  if (kind === "claim") return revision.claims.find(claim => claim.id === id)?.text ?? null;
  if (kind === "event") return revision.timeline_events.find(event => event.id === id)?.description ?? null;
  const edge = revision.graph.edges.find(item => item.id === id);
  if (!edge) return null;
  const name = (nodeId: string) => revision.graph.nodes.find(node => node.id === nodeId)?.label ?? nodeId;
  return edge.description || `${name(edge.source)} → ${name(edge.target)}`;
}

function changeOf(item: ComparisonItem, left: string | null, right: string | null): Change {
  if (!item.base_ref_id) return "added";
  if (!item.compared_ref_id) return "removed";
  const sameSources = item.base_evidence_ids.length === item.compared_evidence_ids.length
    && item.base_evidence_ids.every(id => item.compared_evidence_ids.includes(id));
  return item.support_status === "still_supported" && left === right && sameSources ? "unchanged" : "changed";
}

const ORDER: Record<Change, number> = { changed: 0, removed: 1, added: 2, unchanged: 3 };
const KINDS: Record<ComparisonKind, number> = { claim: 0, edge: 1, event: 2 };

/** Rows for the two columns, changes first, then by kind; the server's order within each. */
export function diffRows(base: AnalysisRevision, compared: AnalysisRevision, comparison: RevisionComparison): DiffRow[] {
  if (comparison.base_analysis_id !== base.analysis_id || comparison.compared_analysis_id !== compared.analysis_id) {
    throw new Error("The comparison is for a different pair of revisions.");
  }
  const rows = comparison.items.map((item, index) => {
    const left = findingText(base, item.kind, item.base_ref_id);
    const right = findingText(compared, item.kind, item.compared_ref_id);
    const row: DiffRow = {
      kind: item.kind,
      change: changeOf(item, left, right),
      status: item.support_status,
      statusLabel: supportLabel(item.support_status),
      left, right,
      leftEvidence: item.base_evidence_ids,
      rightEvidence: item.compared_evidence_ids,
      note: sameFindingNote(item.possible_matches),
      ...(item.explanation ? { explanation: item.explanation } : {}),
    };
    return { row, index };
  });
  rows.sort((a, b) => ORDER[a.row.change] - ORDER[b.row.change] || KINDS[a.row.kind] - KINDS[b.row.kind] || a.index - b.index);
  return rows.map(r => r.row);
}

export function changeCounts(rows: DiffRow[]): Record<Change, number> {
  const counts: Record<Change, number> = { added: 0, removed: 0, changed: 0, unchanged: 0 };
  for (const row of rows) counts[row.change] += 1;
  return counts;
}
