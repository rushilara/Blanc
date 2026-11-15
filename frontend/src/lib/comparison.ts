// Support diff between a revision and the one before it (BLA-34 / BLA-63).
// Status labels stay as the server sent them. A possible match is only a note.

export const SUPPORT_STATUSES = [
  "still_supported",
  "weakened",
  "not_supported",
  "newly_supported",
  "unresolved",
] as const;

export type SupportStatus = (typeof SUPPORT_STATUSES)[number];
export type ComparisonKind = "claim" | "edge" | "event";

export type PossibleMatch = {
  ref_id: string;
  shared_evidence_ids: string[];
  remaining_sources: number;
  original_sources: number;
};

export type ComparisonItem = {
  kind: ComparisonKind;
  base_ref_id: string | null;
  compared_ref_id: string | null;
  support_status: SupportStatus;
  base_evidence_ids: string[];
  compared_evidence_ids: string[];
  explanation?: string;
  possible_matches?: PossibleMatch[];
};

export type RevisionComparison = {
  schema_version: "1.0";
  case_id: string;
  base_analysis_id: string;
  compared_analysis_id: string;
  excluded_record_ids: string[];
  items: ComparisonItem[];
  created_at: string;
};

const LABELS: Record<SupportStatus, string> = {
  still_supported: "Still supported",
  weakened: "Weakened",
  not_supported: "Not supported",
  newly_supported: "Newly supported",
  unresolved: "Unresolved",
};

export function supportLabel(status: SupportStatus): string {
  return LABELS[status];
}

/** A changed finding is anything the comparison did not leave still supported. */
export function supportChanged(status: SupportStatus): boolean {
  return status !== "still_supported";
}

/** Cited sources keep their numbers. Excluded records that are no longer cited follow them, so restore stays reachable. */
export function sourcesInOrder(cited: string[], excluded: string[]): string[] {
  const seen = new Set(cited);
  return [...cited, ...excluded.filter(id => !seen.has(id))];
}

export function itemFor(
  items: ComparisonItem[],
  kind: ComparisonKind,
  id: string,
  side: "base" | "compared",
): ComparisonItem | undefined {
  const key = side === "base" ? "base_ref_id" : "compared_ref_id";
  return items.find(item => item.kind === kind && item[key] === id);
}

/** Advisory only. Does not replace support_status. */
export function sameFindingNote(matches: PossibleMatch[] | undefined): string | null {
  if (!matches?.length) return null;
  return matches
    .map(match => `Possibly the same finding: ${match.remaining_sources} of ${match.original_sources} sources remain — please confirm`)
    .join(" ");
}
