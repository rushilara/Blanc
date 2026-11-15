"use client";

import { useEffect, useId, useRef, useState } from "react";

import type { EvidenceSelection } from "@/lib/evidence-selection";
import {
  fetchRecord,
  RecordFetchError,
  type EvidenceRecord,
} from "@/lib/records";

const LATER = "available in a later step";

type EvidenceDrawerProps = {
  caseId: string;
  selection: EvidenceSelection;
  records?: EvidenceRecord[];
  excludedRecordIds?: string[];
  onClose: () => void;
  onExclude?: (recordId: string) => void;
  onAnnotate?: (recordId: string) => void;
};

type RemoteState =
  | { status: "loading" }
  | { status: "ready"; record: EvidenceRecord; excerpt: string }
  | { status: "error"; message: string };

function resolveFromProp(
  records: EvidenceRecord[] | undefined,
  caseId: string,
  recordId: string,
): EvidenceRecord | undefined {
  return records?.find(
    (record) => record.case_id === caseId && record.record_id === recordId,
  );
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

function RecordDetails({
  record,
  excerpt,
  excluded,
}: {
  record: EvidenceRecord;
  excerpt: string;
  excluded: boolean;
}) {
  return (
    <>
      <div className="flex flex-wrap items-center gap-2">
        <h3
          id={`record-${record.record_id}-title`}
          className="break-all font-mono text-sm font-semibold"
        >
          {record.record_id}
        </h3>
        {excluded && (
          <span className="rounded border border-amber-300 bg-amber-50 px-2 py-0.5 text-xs font-medium text-amber-900 dark:border-amber-800 dark:bg-amber-950 dark:text-amber-100">
            Excluded
          </span>
        )}
      </div>
      <dl className="grid gap-3 text-sm sm:grid-cols-2">
        <div className="space-y-1">
          <dt className="font-semibold text-zinc-500">Source file</dt>
          <dd className="break-all">{record.source_file}</dd>
        </div>
        <div className="space-y-1">
          <dt className="font-semibold text-zinc-500">Locator</dt>
          <dd className="break-all">{record.source_locator}</dd>
        </div>
        <div className="space-y-1">
          <dt className="font-semibold text-zinc-500">Timestamp kind</dt>
          <dd className="capitalize">
            {(record.timestamp_kind ?? "unknown").replaceAll("_", " ")}
          </dd>
        </div>
        {record.timestamp != null && record.timestamp !== "" && (
          <div className="space-y-1">
            <dt className="font-semibold text-zinc-500">Timestamp</dt>
            <dd className="break-words font-mono text-xs">{record.timestamp}</dd>
          </div>
        )}
        <div className="space-y-1 sm:col-span-2">
          <dt className="font-semibold text-zinc-500">Record ID</dt>
          <dd className="break-all font-mono text-xs">{record.record_id}</dd>
        </div>
      </dl>
      <div className="space-y-2">
        <h4 className="text-sm font-semibold">Excerpt</h4>
        <blockquote className="whitespace-pre-wrap break-words rounded-lg bg-zinc-100 p-4 text-sm leading-7 dark:bg-zinc-900">
          {excerpt}
        </blockquote>
      </div>
    </>
  );
}

function RecordSection({
  caseId,
  recordId,
  records,
  excluded,
  onExclude,
  onAnnotate,
}: {
  caseId: string;
  recordId: string;
  records?: EvidenceRecord[];
  excluded: boolean;
  onExclude?: (recordId: string) => void;
  onAnnotate?: (recordId: string) => void;
}) {
  const fromProp = resolveFromProp(records, caseId, recordId);
  const [remote, setRemote] = useState<RemoteState | null>(
    fromProp ? null : { status: "loading" },
  );

  useEffect(() => {
    if (resolveFromProp(records, caseId, recordId)) return;

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
  }, [caseId, recordId, records]);

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
      aria-labelledby={`record-${recordId}-title`}
      className="space-y-4 border-t border-zinc-200 pt-5 first:border-t-0 first:pt-0 dark:border-zinc-800"
    >
      {loading && (
        <>
          <h3
            id={`record-${recordId}-title`}
            className="break-all font-mono text-sm font-semibold"
          >
            {recordId}
          </h3>
          <p role="status" className="text-sm text-zinc-500">
            Loading source record…
          </p>
        </>
      )}

      {error && (
        <>
          <h3
            id={`record-${recordId}-title`}
            className="break-all font-mono text-sm font-semibold"
          >
            {recordId}
          </h3>
          <p role="alert" className="text-sm text-red-700 dark:text-red-300">
            {error}
          </p>
        </>
      )}

      {record && excerpt != null && (
        <RecordDetails record={record} excerpt={excerpt} excluded={excluded} />
      )}

      <RecordActions
        recordId={recordId}
        onExclude={onExclude}
        onAnnotate={onAnnotate}
      />
    </article>
  );
}

export function EvidenceDrawer({
  caseId,
  selection,
  records,
  excludedRecordIds = [],
  onClose,
  onExclude,
  onAnnotate,
}: EvidenceDrawerProps) {
  const dialogRef = useRef<HTMLDialogElement>(null);
  const closeRef = useRef<HTMLButtonElement>(null);
  const onCloseRef = useRef(onClose);
  const titleId = useId();
  const excluded = new Set(excludedRecordIds);
  const evidenceIds = [...new Set(selection.evidenceIds)];

  useEffect(() => {
    onCloseRef.current = onClose;
  }, [onClose]);

  useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog) return;

    if (!dialog.open) {
      dialog.showModal();
    }
    closeRef.current?.focus();

    function handleClose() {
      onCloseRef.current();
    }

    dialog.addEventListener("close", handleClose);
    return () => {
      dialog.removeEventListener("close", handleClose);
      if (dialog.open) dialog.close();
    };
  }, [selection.id]);

  function requestClose() {
    dialogRef.current?.close();
  }

  return (
    <dialog
      ref={dialogRef}
      aria-labelledby={titleId}
      aria-modal="true"
      className="fixed inset-0 m-0 h-dvh max-h-none w-full max-w-none overflow-y-auto border-0 bg-white p-0 text-zinc-900 shadow-xl open:flex open:flex-col backdrop:bg-black/40 dark:bg-zinc-950 dark:text-zinc-100 sm:inset-y-0 sm:left-auto sm:right-0 sm:w-full sm:max-w-lg sm:border-l sm:border-zinc-200 dark:sm:border-zinc-700"
    >
      <header className="sticky top-0 z-10 flex items-start justify-between gap-3 border-b border-zinc-200 bg-white px-5 py-4 dark:border-zinc-800 dark:bg-zinc-950">
        <div className="min-w-0 space-y-1">
          <p className="text-xs font-medium uppercase tracking-widest text-zinc-500">
            Source evidence · {selection.kind}
          </p>
          <h2 id={titleId} className="truncate text-lg font-semibold">
            {selection.label || selection.id}
          </h2>
        </div>
        <button
          ref={closeRef}
          type="button"
          onClick={requestClose}
          className="shrink-0 rounded border border-zinc-300 px-3 py-2 text-sm dark:border-zinc-700"
        >
          Close
        </button>
      </header>

      <div className="space-y-5 px-5 py-5">
        {evidenceIds.length === 0 ? (
          <p role="status" className="text-sm text-zinc-500">
            This {selection.kind} does not cite any source records.
          </p>
        ) : (
          evidenceIds.map((recordId) => (
            <RecordSection
              key={recordId}
              caseId={caseId}
              recordId={recordId}
              records={records}
              excluded={excluded.has(recordId)}
              onExclude={onExclude}
              onAnnotate={onAnnotate}
            />
          ))
        )}
      </div>
    </dialog>
  );
}
