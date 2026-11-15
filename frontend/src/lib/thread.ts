import type { AnalysisRevision } from "./analysis";

const sameQuestion = (a: string, b: string) => a.trim().replace(/\s+/g, " ").toLowerCase() === b.trim().replace(/\s+/g, " ").toLowerCase();

/**
 * The earlier answers to show above ``current`` as a follow-up thread.
 *
 * ``ancestors`` is the revision chain oldest first, ending with ``current``'s parent.
 * Consecutive revisions that ask the same question (an exclusion, a wider evidence
 * window) are one answer in the thread: only the latest of each run is shown. The run
 * that ends in ``current``'s own question is left out, since ``current`` shows it.
 */
export function earlierTurns(ancestors: AnalysisRevision[], current: AnalysisRevision): AnalysisRevision[] {
  const runs: AnalysisRevision[] = [];
  for (const revision of ancestors) {
    const last = runs.at(-1);
    if (last && sameQuestion(last.question, revision.question)) runs[runs.length - 1] = revision;
    else runs.push(revision);
  }
  const tail = runs.at(-1);
  if (tail && sameQuestion(tail.question, current.question)) runs.pop();
  return runs;
}

/** The label above a turn: "Question" for the first, then "Follow-up 1", "Follow-up 2"… */
export const turnLabel = (index: number) => (index === 0 ? "Question" : `Follow-up ${index}`);
