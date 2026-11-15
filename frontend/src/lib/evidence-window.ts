// What the investigator sees about the evidence window (BLA-65). The server decides
// whether more evidence is available; this only turns that record into words.
// Skipped records stay IDs and ranks. Their content is never read here.
import type { EvidenceWindow, RetrievalCoverage, SkipReason } from "./analysis";

export const TRADEOFFS = [
  ["Slower answer.", "The model reads more text."],
  ["Can be less precise.", "More, weaker evidence can make citations less focused. More isn't always better."],
  ["More evidence text sent to Gemini.", "Storage is local, but Gemini processes the evidence text for this answer."],
] as const;

/** Revisions saved before coverage existed ran in the standard window. */
export function windowLabel(window?: EvidenceWindow | null, maxRecords?: number | null): string {
  const name = window === "expanded" ? "Expanded" : "Standard";
  return typeof maxRecords === "number" ? `${name} · ${maxRecords} records` : name;
}

/** Set only when the server flag is set. The count is the records it left out. */
export function offerCopy(coverage: RetrievalCoverage | undefined): string | null {
  if (!coverage?.offer_expanded_window) return null;
  const count = coverage.left_out_record_ids.length;
  const noun = count === 1 ? "record was" : "records were";
  return `${count} more relevant ${noun} left out of this answer. Want more evidence?`;
}

/** An expanded run that is still short. There is no further step. */
export function stillLeftOutCopy(coverage: RetrievalCoverage | undefined): string | null {
  if (!coverage || coverage.offer_expanded_window) return null;
  const count = coverage.left_out_record_ids.length;
  if (count === 0) return null;
  return count === 1 ? "1 relevant record was still left out." : `${count} relevant records were still left out.`;
}

/** Budget skips, with nothing left out by the record limit and no offer. */
export function budgetNote(coverage: RetrievalCoverage | undefined): string | null {
  if (!coverage || coverage.offer_expanded_window || coverage.left_out_record_ids.length > 0) return null;
  const reasons = new Set(coverage.skipped.map(skip => skip.reason));
  if (!reasons.has("budget") || reasons.has("record_limit")) return null;
  return "Some evidence didn't fit in this answer.";
}

/** Plain wording for each skip reason (BLA-73). */
export const SKIP_REASON_TEXT: Record<SkipReason, string> = {
  budget: "didn't fit the character budget",
  record_limit: "ranked past the record limit",
  media_limit: "left out: media cap reached",
  media_too_large: "file too large to send",
  media: "file missing or failed integrity check",
};

export type SkippedLine = { recordId: string; rank: number; why: string; modality?: "image" | "pdf" };

export function skippedLines(coverage: RetrievalCoverage | undefined): SkippedLine[] {
  if (!coverage) return [];
  return [...coverage.skipped]
    .sort((a, b) => a.rank - b.rank)
    .map(skip => ({
      recordId: skip.record_id,
      rank: skip.rank,
      // An unknown reason from a newer server still gets a line, never a crash.
      why: SKIP_REASON_TEXT[skip.reason] ?? "left out",
      ...(skip.modality ? { modality: skip.modality } : {}),
    }));
}
