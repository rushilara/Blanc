"use client";

import { useEffect, useId, useRef, useState } from "react";

import { SourceContent } from "@/components/SourceContent";
import type { EvidenceRecord } from "@/lib/analysis";
import { fetchRecord, RecordFetchError } from "@/lib/records";

const LATER = "available in a later step";

type SourceReferencesProps = {
  ids: string[];
  caseId: string;
  records: EvidenceRecord[];
  autoOpen?: boolean;
  /** When set, render this label as the open control instead of citation chips (timeline events). */
  triggerLabel?: string;
  /** When true, render only the evidence dialog, with no chips or label (graph click-to-open). */
  hideTriggers?: boolean;
  excludedRecordIds?: string[];
  onExclude?: (recordId: string) => void;
  onAnnotate?: (recordId: string) => void;
  /** Called after the dialog closes, so a caller that opened it can reset. */
  onClose?: () => void;
};

type RemoteState =
  | { status: "loading" }
  | { status: "ready"; record: EvidenceRecord; excerpt: string }
  | { status: "error"; message: string };

function resolveFromProp(
  records: EvidenceRecord[],
  caseId: string,
  recordId: string,
): EvidenceRecord | undefined {
  const matches = records.filter(
    (record) => record.case_id === caseId && record.record_id === recordId,
  );
  return matches.length === 1 ? matches[0] : undefined;
}

function RecordActions({
  recordId,
  onExclude,
  onAnnotate,
}: {
  recordId: string;
  onExclude?: (recordId: string) => void;
  onAnnotate?: (recordId: string) => void;
}) {
  return (
    <>
      <div className="flex flex-wrap gap-2">
        <button
          type="button"
          disabled={!onExclude}
          title={!onExclude ? LATER : "Exclude this record from the investigation"}
          aria-description={!onExclude ? LATER : undefined}
          onClick={() => onExclude?.(recordId)}
          className="rounded border border-zinc-300 px-3 py-2 text-sm disabled:cursor-not-allowed disabled:opacity-50 dark:border-zinc-700"
        >
          Exclude
        </button>
        <button
          type="button"
          disabled={!onAnnotate}
          title={!onAnnotate ? LATER : "Annotate this record"}
          aria-description={!onAnnotate ? LATER : undefined}
          onClick={() => onAnnotate?.(recordId)}
          className="rounded border border-zinc-300 px-3 py-2 text-sm disabled:cursor-not-allowed disabled:opacity-50 dark:border-zinc-700"
        >
          Annotate
        </button>
      </div>
      {(!onExclude || !onAnnotate) && (
        <p className="text-xs text-zinc-500">{LATER}</p>
      )}
    </>
  );
}

function RecordSection({
  caseId,
  recordId,
  records,
  excluded,
  highlighted,
  onExclude,
  onAnnotate,
}: {
  caseId: string;
  recordId: string;
  records: EvidenceRecord[];
  excluded: boolean;
  highlighted: boolean;
  onExclude?: (recordId: string) => void;
  onAnnotate?: (recordId: string) => void;
}) {
  const fromProp = resolveFromProp(records, caseId, recordId);
  const hasPropRecord = fromProp != null;
  const [remote, setRemote] = useState<RemoteState | null>(
    hasPropRecord ? null : { status: "loading" },
  );
  const sectionRef = useRef<HTMLElement>(null);

  // Depend on caseId/recordId and whether the prop already has the record — not the
  // records array identity — so parent re-renders do not abort/refetch while open.
  useEffect(() => {
    if (hasPropRecord) return;

    const controller = new AbortController();
    fetchRecord(caseId, recordId, controller.signal)
      .then((read) => {
        setRemote({
          status: "ready",
          record: read.record,
          excerpt: read.excerpt?.text ?? read.record.content,
        });
      })
      .catch((err: unknown) => {
        if (controller.signal.aborted) return;
        const message =
          err instanceof RecordFetchError
            ? err.message
            : "Could not load this source record.";
        setRemote({ status: "error", message });
      });

    return () => controller.abort();
  }, [caseId, recordId, hasPropRecord]);

  useEffect(() => {
    if (highlighted) sectionRef.current?.scrollIntoView({ block: "nearest" });
  }, [highlighted]);

  const record = fromProp ?? (remote?.status === "ready" ? remote.record : null);
  const excerpt = fromProp
    ? fromProp.content
    : remote?.status === "ready"
      ? remote.excerpt
      : null;
  const loading = !fromProp && (remote === null || remote.status === "loading");
  const error =
    !fromProp && remote?.status === "error" ? remote.message : null;

  return (
    <article
      ref={sectionRef}
      aria-labelledby={`record-${recordId}-title`}
      className={`space-y-4 border-t border-zinc-200 pt-5 first:border-t-0 first:pt-0 dark:border-zinc-800 ${highlighted ? "rounded-lg ring-2 ring-blue-400 ring-offset-2 dark:ring-offset-zinc-950" : ""}`}
    >
      <div className="flex flex-wrap items-center gap-2">
        <h3
          id={`record-${recordId}-title`}
          className="break-all font-mono text-sm font-semibold"
        >
          {recordId}
        </h3>
        {excluded && (
          <span className="rounded border border-amber-300 bg-amber-50 px-2 py-0.5 text-xs font-medium text-amber-900 dark:border-amber-800 dark:bg-amber-950 dark:text-amber-100">
            Excluded
          </span>
        )}
      </div>

      {loading && (
        <p role="status" className="text-sm text-zinc-500">
          Loading source record…
        </p>
      )}

      {error && (
        <p role="alert" className="text-sm text-red-700 dark:text-red-300">
          {error}
        </p>
      )}

      {record && excerpt != null && (
        <>
          <dl className="grid gap-3 text-sm sm:grid-cols-2">
            <div className="space-y-1">
              <dt className="font-semibold text-zinc-500">Original file</dt>
              <dd className="break-all">{record.source_file}</dd>
            </div>
            <div className="space-y-1">
              <dt className="font-semibold text-zinc-500">Locator</dt>
              <dd className="break-all">{record.source_locator}</dd>
            </div>
            <div className="space-y-1">
              <dt className="font-semibold text-zinc-500">Source timestamp</dt>
              <dd className="break-words">
                {record.timestamp ?? "No normalized timestamp"} · {record.timestamp_kind ?? "unknown"}
              </dd>
            </div>
            <div className="space-y-1 sm:col-span-2">
              <dt className="font-semibold text-zinc-500">Record ID</dt>
              <dd className="break-all font-mono text-xs">{record.record_id}</dd>
            </div>
          </dl>
          {/* #44's readable view with the raw record preserved. Revisions cite whole
              records, so the excerpt shown is the record's content. */}
          <SourceContent key={record.record_id} record={{ ...record, content: excerpt }} />
        </>
      )}

      {!loading && !error && !record && (
        <p role="status" className="text-sm leading-7">
          This source is unavailable for the current case. Its content cannot be verified here.
        </p>
      )}

      <RecordActions
        recordId={recordId}
        onExclude={onExclude}
        onAnnotate={onAnnotate}
      />
    </article>
  );
}

export function SourceReferences({
  ids,
  caseId,
  records,
  autoOpen = false,
  triggerLabel,
  hideTriggers = false,
  excludedRecordIds = [],
  onExclude,
  onAnnotate,
  onClose,
}: SourceReferencesProps) {
  const dialog = useRef<HTMLDialogElement>(null);
  const closeRef = useRef<HTMLButtonElement>(null);
  const titleId = useId();
  const uniqueIds = [...new Set(ids)];
  const [openFor, setOpenFor] = useState<string | null>(
    autoOpen ? uniqueIds[0] ?? null : null,
  );
  const [dialogOpen, setDialogOpen] = useState(autoOpen && uniqueIds.length > 0);
  const excluded = new Set(excludedRecordIds);

  // Open when autoOpen flips on or the investigator clicks a chip/label. Do not
  // call dialog.close() in cleanup: under React StrictMode (npm run dev) the
  // close event can fire after remount and clear open state.
  useEffect(() => {
    if (!dialogOpen) return;
    const el = dialog.current;
    if (!el) return;
    if (!el.open) el.showModal();
    closeRef.current?.focus();
  }, [dialogOpen, openFor]);

  function open(id: string) {
    setOpenFor(id);
    setDialogOpen(true);
  }

  return (
    <>
      {hideTriggers ? null : triggerLabel ? (
        <button
          type="button"
          disabled={!uniqueIds.length}
          onClick={() => open(uniqueIds[0]!)}
          aria-haspopup="dialog"
          aria-label={`Inspect evidence for event: ${triggerLabel}`}
          className="block w-full break-words text-left text-sm leading-6 underline decoration-blue-400 underline-offset-4 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-blue-600 disabled:opacity-50"
        >
          {triggerLabel}
        </button>
      ) : (
        <ul aria-label="Source record references" className="flex flex-wrap gap-1.5">
          {uniqueIds.map((id) => (
            <li key={id}>
              <button
                type="button"
                onClick={() => open(id)}
                aria-haspopup="dialog"
                aria-label={`Inspect source ${id}`}
                className="rounded-md border border-blue-200 bg-blue-50 px-2 py-1 font-mono text-xs text-blue-800 underline-offset-2 hover:underline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-blue-600 dark:border-blue-800 dark:bg-blue-950 dark:text-blue-200"
              >
                {id}
              </button>
            </li>
          ))}
        </ul>
      )}
      {dialogOpen && (
        <dialog
          ref={dialog}
          aria-labelledby={titleId}
          aria-modal="true"
          onClose={() => {
            setDialogOpen(false);
            onClose?.();
          }}
          className="fixed inset-0 m-0 h-dvh max-h-none w-full max-w-none overflow-y-auto border-0 bg-white p-0 text-zinc-900 shadow-xl open:flex open:flex-col backdrop:bg-black/40 dark:bg-zinc-950 dark:text-zinc-100 sm:inset-y-0 sm:left-auto sm:right-0 sm:w-full sm:max-w-lg sm:border-l sm:border-zinc-200 dark:sm:border-zinc-700"
        >
          <header className="sticky top-0 z-10 flex items-start justify-between gap-3 border-b border-zinc-200 bg-white px-5 py-4 dark:border-zinc-800 dark:bg-zinc-950">
            <div className="min-w-0 space-y-1">
              <p className="text-xs font-medium uppercase tracking-widest text-zinc-500">
                Source evidence
              </p>
              <h2 id={titleId} className="truncate text-lg font-semibold">
                {uniqueIds.length === 1
                  ? (openFor ?? uniqueIds[0])
                  : `${uniqueIds.length} cited records`}
              </h2>
            </div>
            <button
              ref={closeRef}
              type="button"
              onClick={() => dialog.current?.close()}
              className="shrink-0 rounded border border-zinc-300 px-3 py-2 text-sm dark:border-zinc-700"
            >
              Close
            </button>
          </header>

          <div className="space-y-5 px-5 py-5">
            {uniqueIds.length === 0 ? (
              <p role="status" className="text-sm text-zinc-500">
                No source records are cited here.
              </p>
            ) : (
              uniqueIds.map((recordId) => (
                <RecordSection
                  key={recordId}
                  caseId={caseId}
                  recordId={recordId}
                  records={records}
                  excluded={excluded.has(recordId)}
                  highlighted={recordId === openFor}
                  onExclude={onExclude}
                  onAnnotate={onAnnotate}
                />
              ))
            )}
          </div>
        </dialog>
      )}
    </>
  );
}
