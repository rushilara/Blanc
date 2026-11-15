"use client";

import { useEffect, useState } from "react";
import type { AnalysisRevision } from "@/lib/analysis";
import { ApiError, getComparison, getRevision } from "@/lib/live-api";
import { changeCounts, diffRows, evidenceState, orderPair, type Change, type DiffRow } from "@/lib/revision-diff";

// BLA-50: any two saved revisions of one question, side by side. The older one is on the
// left. Support statuses come from the server's comparison (BLA-34); this view only lays
// them out and marks what was added, removed or changed.

type Loaded = { base: AnalysisRevision; compared: AnalysisRevision; rows: DiffRow[] };

const CHANGE: Record<Change, { label: string; className: string }> = {
  changed: { label: "Changed", className: "border-amber-500 bg-amber-50 dark:bg-amber-950/40" },
  removed: { label: "Removed", className: "border-red-500 bg-red-50 dark:bg-red-950/40" },
  added: { label: "Added", className: "border-emerald-600 bg-emerald-50 dark:bg-emerald-950/40" },
  unchanged: { label: "Unchanged", className: "border-zinc-300 dark:border-zinc-700" },
};

export function RevisionDiff({ caseId, pair, onClose }: { caseId: string; pair: [string, string]; onClose: () => void }) {
  const [loaded, setLoaded] = useState<Loaded | null>(null);
  const [error, setError] = useState("");
  const [first, second] = pair;

  useEffect(() => {
    const controller = new AbortController();
    (async () => {
      try {
        const [a, b] = await Promise.all([getRevision(caseId, first, controller.signal), getRevision(caseId, second, controller.signal)]);
        const [base, compared] = orderPair(a, b);
        const comparison = await getComparison(caseId, compared.analysis_id, controller.signal, base.analysis_id);
        if (!controller.signal.aborted) setLoaded({ base, compared, rows: diffRows(base, compared, comparison) });
      } catch (e) {
        if (!controller.signal.aborted) setError(e instanceof ApiError ? e.message : "The two versions could not be compared.");
      }
    })();
    return () => controller.abort();
  }, [caseId, first, second]);

  const counts = loaded ? changeCounts(loaded.rows) : null;
  return (
    <section aria-label="Compare two versions" className="mx-auto w-full max-w-7xl space-y-4 p-4 sm:p-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h2 className="text-xl font-semibold">Compare two versions</h2>
        <button type="button" onClick={onClose} className="rounded border border-zinc-400 px-3 py-2 text-sm">Close comparison</button>
      </div>
      {!loaded && !error && <p role="status">Loading both versions and their comparison…</p>}
      {error && <p role="alert" className="rounded border border-red-400 p-4 text-sm [overflow-wrap:anywhere]">{error}</p>}
      {loaded && counts && <>
        <p className="text-sm [overflow-wrap:anywhere]"><span className="font-semibold">Question: </span>{loaded.base.question}</p>
        <p className="text-sm" aria-label="Change summary">
          {counts.changed} changed · {counts.removed} removed · {counts.added} added · {counts.unchanged} unchanged.
          {" "}Support labels come from the server&apos;s comparison. A &ldquo;possibly the same finding&rdquo; note is a suggestion to check, not a match.
        </p>
        <div className="grid gap-4 md:grid-cols-2">
          <Side title="Earlier version" revision={loaded.base} />
          <Side title="Later version" revision={loaded.compared} />
        </div>
        <ol className="space-y-3" aria-label="Findings side by side">
          {loaded.rows.map((row, index) => <li key={index} data-change={row.change} className={`rounded-lg border-l-4 p-3 text-sm ${CHANGE[row.change].className}`}>
            <p className="mb-2 flex flex-wrap gap-x-3 text-xs font-semibold uppercase tracking-wide">
              <span>{CHANGE[row.change].label}</span><span>{row.kind}</span><span>{row.statusLabel}</span>
            </p>
            <div className="grid gap-3 md:grid-cols-2">
              <Finding text={row.left} evidence={row.leftEvidence} empty="Not in the earlier version" />
              <Finding text={row.right} evidence={row.rightEvidence} empty="Not in the later version" />
            </div>
            {row.explanation && <p className="mt-2 text-xs text-zinc-600 dark:text-zinc-400 [overflow-wrap:anywhere]">{row.explanation}</p>}
            {row.note && <p className="mt-2 text-xs italic [overflow-wrap:anywhere]">{row.note}</p>}
          </li>)}
        </ol>
      </>}
    </section>
  );
}

function Side({ title, revision }: { title: string; revision: AnalysisRevision }) {
  const { included, excluded } = evidenceState(revision);
  return (
    <div className="space-y-2 rounded-lg border border-zinc-300 p-4 text-sm dark:border-zinc-700">
      <h3 className="font-semibold">{title}</h3>
      <p className="break-all font-mono text-xs">{revision.analysis_id} · <time dateTime={revision.created_at}>{revision.created_at}</time></p>
      <p><span className="font-semibold">Included evidence ({included.length}): </span>
        <span className="break-all font-mono text-xs">{included.join(", ") || "None"}</span></p>
      <p><span className="font-semibold">Excluded evidence ({excluded.length}): </span>
        <span className="break-all font-mono text-xs">{excluded.join(", ") || "None"}</span></p>
    </div>
  );
}

function Finding({ text, evidence, empty }: { text: string | null; evidence: string[]; empty: string }) {
  if (text === null) return <p className="text-xs italic text-zinc-500">{empty}</p>;
  return (
    <div className="space-y-1">
      <p className="[overflow-wrap:anywhere]">{text}</p>
      <p className="break-all font-mono text-xs text-zinc-600 dark:text-zinc-400">Sources: {evidence.join(", ") || "none"}</p>
    </div>
  );
}
