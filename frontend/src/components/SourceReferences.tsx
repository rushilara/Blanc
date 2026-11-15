"use client";

import { useEffect, useRef, useState } from "react";
import type { EvidenceRecord } from "@/lib/analysis";
import { SourceContent } from "@/components/SourceContent";

export function SourceReferences({ ids, caseId, records, autoOpen = false, triggerLabel, hideTriggers = false }: {
  ids: string[];
  caseId: string;
  records: EvidenceRecord[];
  autoOpen?: boolean;
  triggerLabel?: string;
  /** When true, only the evidence dialog is rendered (used by graph click-to-open). */
  hideTriggers?: boolean;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const [selected, setSelected] = useState<string | null>(autoOpen ? ids[0] ?? null : null);
  useEffect(() => { if (autoOpen && !dialog.current?.open) dialog.current?.showModal(); }, [autoOpen]);
  const matches = records.filter((record) => record.case_id === caseId && record.record_id === selected);
  const record = matches.length === 1 ? matches[0] : undefined;

  function open(id: string) {
    setSelected(id);
    dialog.current?.showModal();
  }

  return (
    <>
      {!hideTriggers && (triggerLabel ? <button type="button" disabled={!ids.length} onClick={() => open(ids[0])} aria-haspopup="dialog"
        aria-label={`Inspect evidence for event: ${triggerLabel}`}
        className="block w-full break-words text-left text-sm leading-6 underline decoration-blue-400 underline-offset-4 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-blue-600 disabled:opacity-50">{triggerLabel}</button> : <ul aria-label="Source record references" className="flex flex-wrap gap-1.5">
        {[...new Set(ids)].map((id) => (
          <li key={id}>
            <button type="button" onClick={() => open(id)} aria-haspopup="dialog"
              aria-label={`Inspect source ${id}`}
              className="rounded-md border border-blue-200 bg-blue-50 px-2 py-1 font-mono text-xs text-blue-800 underline-offset-2 hover:underline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-blue-600 dark:border-blue-800 dark:bg-blue-950 dark:text-blue-200">
              {id}
            </button>
          </li>
        ))}
      </ul>)}
      <dialog ref={dialog} aria-label="Source evidence" className="fixed inset-y-0 right-0 left-auto m-0 h-dvh max-h-none w-full max-w-lg overflow-y-auto border-l border-zinc-200 bg-white p-5 text-zinc-900 shadow-xl backdrop:bg-black/40 dark:border-zinc-700 dark:bg-zinc-950 dark:text-zinc-100">
        <div className="mb-6 flex items-center justify-between gap-3">
          <h2 className="text-lg font-semibold">Source evidence</h2>
          <button type="button" onClick={() => dialog.current?.close()} className="rounded border border-zinc-300 px-3 py-2 text-sm dark:border-zinc-700">Close</button>
        </div>
        <p className="mb-4 break-all font-mono text-sm">{selected}</p>
        {ids.length > 1 && <nav aria-label="Sources for this finding" className="mb-4 flex flex-wrap gap-2">{[...new Set(ids)].map(id => <button key={id} type="button" aria-pressed={id === selected} onClick={() => setSelected(id)} className="break-all rounded border border-zinc-400 px-2 py-1 font-mono text-xs aria-pressed:bg-blue-100 aria-pressed:text-blue-950">{id}</button>)}</nav>}
        {record ? (
          <div className="space-y-5">
            <dl className="space-y-2 text-sm">
              <dt className="font-semibold">Original file</dt><dd className="break-all">{record.source_file}</dd>
              <dt className="font-semibold">Locator</dt><dd className="break-all">{record.source_locator}</dd>
              <dt className="font-semibold">Source timestamp</dt><dd className="break-words">{record.timestamp ?? "No normalized timestamp"} · {record.timestamp_kind ?? "unknown"}</dd>
            </dl>
            <SourceContent key={record.record_id} record={record} />
          </div>
        ) : (
          <p role="status" className="text-sm leading-7">This source is unavailable for the current case. Its content cannot be verified here.</p>
        )}
      </dialog>
    </>
  );
}
