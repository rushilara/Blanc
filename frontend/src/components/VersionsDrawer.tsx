"use client";

import { useEffect, useRef, useState } from "react";
import { ApiError, listRevisions, type RevisionSummary } from "@/lib/live-api";
import { investigationRoot } from "@/lib/revision-history";

export function VersionsDrawer({ caseId, caseTitle, activeId, busy, onSelect, onCompare }: {
  caseId: string; caseTitle: string; activeId?: string; busy: boolean;
  onSelect: (analysisId: string) => Promise<boolean>;
  /** BLA-50: open two picked versions side by side. */
  onCompare?: (pair: [string, string]) => void;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const request = useRef<AbortController | null>(null);
  const [revisions, setRevisions] = useState<RevisionSummary[]>([]);
  const [state, setState] = useState<"loading" | "ready" | "error">("loading");
  const [error, setError] = useState("");
  const [investigation, setInvestigation] = useState("");
  // Up to two versions picked for comparison; picking a third drops the oldest pick.
  const [picked, setPicked] = useState<string[]>([]);
  useEffect(() => () => request.current?.abort(), []);

  async function load() {
    request.current?.abort();
    const controller = new AbortController(); request.current = controller;
    setState("loading"); setError(""); setRevisions([]); setPicked([]);
    try {
      const items = await listRevisions(caseId, controller.signal);
      if (!controller.signal.aborted) {
        setRevisions(items);
        setInvestigation(activeId ? investigationRoot(items, activeId) ?? "" : "");
        setState("ready");
      }
    } catch (e) {
      if (!controller.signal.aborted) {
        setError(e instanceof ApiError ? e.message : "Could not load saved versions. Check the backend connection.");
        setState("error");
      }
    }
  }
  const roots = revisions.filter(r => r.parent_analysis_id === null);
  const pickedRevisions = picked.map(id => revisions.find(r => r.analysis_id === id)).filter(r => r !== undefined);
  const sameQuestion = pickedRevisions.length === 2 && pickedRevisions[0].question === pickedRevisions[1].question;
  const togglePick = (id: string) => setPicked(current => current.includes(id) ? current.filter(x => x !== id) : [...current, id].slice(-2));
  const visible = investigation ? revisions.filter(r => investigationRoot(revisions, r.analysis_id) === investigation) : revisions;
  return <>
    <button type="button" disabled={busy} aria-haspopup="dialog" onClick={() => { dialog.current?.showModal(); void load(); }}
      className="rounded border border-zinc-400 px-4 py-2 text-sm font-semibold disabled:opacity-40">Saved versions</button>
    <dialog ref={dialog} aria-label="Saved versions" onClose={() => request.current?.abort()}
      className="fixed inset-y-0 right-0 left-auto m-0 h-dvh max-h-none w-full max-w-xl overflow-y-auto border-l border-zinc-300 bg-white p-5 text-zinc-900 shadow-xl backdrop:bg-black/40 dark:border-zinc-700 dark:bg-zinc-950 dark:text-zinc-100">
      <div className="mb-4 flex items-center justify-between gap-4">
        <h2 className="text-xl font-semibold">Saved versions</h2>
        <button type="button" onClick={() => dialog.current?.close()} className="rounded border border-zinc-400 px-3 py-2 text-sm">Close</button>
      </div>
      <p className="mb-4 text-sm [overflow-wrap:anywhere]">{caseTitle}</p>
      <p className="mb-5 text-xs text-zinc-500 dark:text-zinc-400">Open a saved version with its original sources. This does not generate a new answer.</p>
      {state === "loading" && <p role="status">Loading saved versions…</p>}
      {state === "error" && <div role="alert" className="space-y-3 text-sm"><p>{error}</p><button className="underline" onClick={() => void load()}>Retry history</button></div>}
      {state === "ready" && <>
        {!revisions.length ? <p role="status">No saved versions in this case yet. Generate an analysis to create one.</p> : <>
          <label className="mb-5 block space-y-2 text-sm"><span className="font-semibold">Investigation</span>
            <select aria-label="Investigation" value={investigation} disabled={busy} onChange={e => setInvestigation(e.target.value)} className="w-full rounded border border-zinc-400 bg-white p-2 dark:bg-zinc-900">
              <option value="">All investigations in this case</option>
              {roots.map(root => <option key={root.analysis_id} value={root.analysis_id}>{root.question} · {root.analysis_id}</option>)}
            </select>
          </label>
          {onCompare && <div className="mb-5 space-y-2 rounded-lg border border-zinc-300 p-3 text-sm dark:border-zinc-700">
            <p>Pick two versions of the same question to see them side by side ({picked.length} of 2 picked).</p>
            {pickedRevisions.length === 2 && !sameQuestion && <p role="alert" className="text-xs text-red-700 dark:text-red-300">These versions ask different questions, so their support cannot be compared. Pick two versions of one question.</p>}
            <button type="button" disabled={busy || !sameQuestion}
              className="rounded border border-blue-500 px-3 py-2 font-semibold text-blue-800 disabled:opacity-50 dark:text-blue-300"
              onClick={() => { if (sameQuestion) { onCompare([picked[0], picked[1]]); dialog.current?.close(); } }}>
              Compare side by side
            </button>
          </div>}
          <ol className="space-y-4" aria-label="Revision history">
            {visible.map(revision => <li key={revision.analysis_id} data-revision-id={revision.analysis_id} className="space-y-3 rounded-lg border border-zinc-300 p-4 text-sm dark:border-zinc-700">
              <p className="font-semibold [overflow-wrap:anywhere]">{revision.question}</p>
              <time dateTime={revision.created_at} className="block break-all font-mono text-xs">{revision.created_at}</time>
              <dl className="space-y-1 text-xs [overflow-wrap:anywhere]">
                <div><dt className="inline font-semibold">Version: </dt><dd className="inline font-mono">{revision.analysis_id}</dd></div>
                <div><dt className="inline font-semibold">Parent: </dt><dd className="inline font-mono">{revision.parent_analysis_id ?? "Original version"}</dd></div>
                {revision.restored_from_analysis_id && <div><dt className="inline font-semibold">Restored from: </dt><dd className="inline font-mono">{revision.restored_from_analysis_id}</dd></div>}
              </dl>
              <div><p className="font-semibold">Excluded records ({revision.excluded_record_ids.length})</p>
                {revision.excluded_record_ids.length ? <ul className="mt-1 space-y-1 font-mono text-xs [overflow-wrap:anywhere]">{revision.excluded_record_ids.map(id => <li key={id}>{id}</li>)}</ul> : <p className="text-xs text-zinc-500">None</p>}
              </div>
              {revision.needs_review && <p className="text-xs font-semibold text-amber-800 dark:text-amber-300">Needs investigator review</p>}
              {onCompare && <label className="flex items-center gap-2 text-xs font-semibold">
                <input type="checkbox" checked={picked.includes(revision.analysis_id)} disabled={busy} onChange={() => togglePick(revision.analysis_id)} />
                Compare
              </label>}
              <button type="button" disabled={busy || activeId === revision.analysis_id} aria-current={activeId === revision.analysis_id ? "true" : undefined}
                className="rounded border border-blue-500 px-3 py-2 font-semibold text-blue-800 disabled:opacity-50 dark:text-blue-300"
                onClick={async () => { setError(""); if (await onSelect(revision.analysis_id)) dialog.current?.close(); else if (dialog.current?.open) setError("Could not open this version. Your current view has been kept. Try again."); }}>
                {activeId === revision.analysis_id ? "Current version" : "Open version"}
              </button>
            </li>)}
          </ol>
        </>}
      </>}
      {busy && <p role="status" className="mt-4 text-sm">Opening saved version and original sources…</p>}
      {state !== "error" && error && <p role="alert" className="mt-4 text-sm text-red-700 dark:text-red-300">{error}</p>}
    </dialog>
  </>;
}
