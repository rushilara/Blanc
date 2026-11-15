"use client";

import { useEffect, useRef, useState, type ReactNode } from "react";
import type { EvidenceRecord } from "@/lib/analysis";
import { fetchRecord, RecordFetchError } from "@/lib/records";
import { isRetryableStatus } from "@/lib/retryable";
import { presentSource } from "@/lib/source-presentation";
import {
  interpretMediaResponse,
  localImageName,
  mediaCaption,
  mediaUrl,
  remoteImageAttachments,
} from "@/lib/source-media";

/** "Open in Google Maps" for Maps links, else "Open <host>". */
function linkLabel(url: string): string {
  try {
    const { hostname, pathname } = new URL(url);
    if (/(^|\.)google\./i.test(hostname) && pathname.startsWith("/maps")) return "Open in Google Maps";
    return `Open ${hostname.replace(/^www\./, "")}`;
  } catch {
    return "Open link";
  }
}

type Remote =
  | { status: "loading" }
  | { status: "ready"; record: EvidenceRecord }
  | { status: "error"; message: string; retryable: boolean };

/**
 * A non-modal panel docked beside the findings, so the investigator reads the source
 * and the answer side by side. Shows the record readably (presentSource), its
 * provenance, and the raw record. A record that was not preloaded is fetched through
 * the case-scoped record endpoint, like the shared drawer does.
 */
export function SourcePanel({ caseId, recordId, number, total, records, excluded, busy, onClose, onStep, onExclude, onRestore }: {
  caseId: string;
  recordId: string;
  number: number;
  total: number;
  records: EvidenceRecord[];
  excluded: boolean;
  busy?: boolean;
  onClose: () => void;
  onStep: (direction: -1 | 1) => void;
  onExclude?: () => void;
  onRestore?: () => void;
}) {
  const preloaded = records.find(item => item.case_id === caseId && item.record_id === recordId);
  const [remote, setRemote] = useState<Remote>({ status: "loading" });
  const [reload, setReload] = useState(0);
  const heading = useRef<HTMLHeadingElement>(null);

  useEffect(() => {
    if (preloaded) return;
    const controller = new AbortController();
    fetchRecord(caseId, recordId, controller.signal)
      .then(read => setRemote({ status: "ready", record: read.record }))
      .catch((error: unknown) => {
        if (controller.signal.aborted) return;
        const status = error instanceof RecordFetchError ? error.status : null;
        setRemote({
          status: "error",
          message: error instanceof RecordFetchError ? error.message : "Could not load this source record.",
          retryable: isRetryableStatus(status),
        });
      });
    return () => controller.abort();
  }, [caseId, recordId, preloaded, reload]);

  useEffect(() => { heading.current?.focus({ preventScroll: true }); }, [recordId]);

  useEffect(() => {
    // An open image viewer handles its own Escape first.
    const onKey = (event: KeyboardEvent) => { if (event.key === "Escape" && !document.querySelector("dialog[open]")) onClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const record = preloaded ?? (remote.status === "ready" ? remote.record : undefined);
  const view = record ? presentSource(record) : null;
  const name = view?.name ?? (record ? record.source_file.split(/[\\/]/).filter(Boolean).at(-1) : undefined) ?? recordId;

  return <aside className="blanc-source-panel" aria-labelledby="blanc-source-panel-title">
    <header>
      <div className="blanc-source-panel-nav">
        <span>Source {number} of {total}</span>
        <span>
          <button type="button" onClick={() => onStep(-1)} disabled={number <= 1} aria-label="Previous source">‹</button>
          <button type="button" onClick={() => onStep(1)} disabled={number >= total} aria-label="Next source">›</button>
          <button type="button" onClick={onClose} aria-label="Close source">×</button>
        </span>
      </div>
      <h2 id="blanc-source-panel-title" ref={heading} tabIndex={-1}>{name}</h2>
      <p className="blanc-source-panel-kind">{view?.kind ?? "Evidence record"}{excluded && <em> · Excluded from this revision</em>}</p>
    </header>

    {!record && remote.status === "loading" && <p role="status">Loading the original record…</p>}
    {!record && remote.status === "error" && <p role="alert">{remote.message} Its content cannot be verified here.{remote.retryable && <>{" "}
      <button type="button" className="blanc-retry" onClick={() => { setRemote({ status: "loading" }); setReload(n => n + 1); }}>Retry</button></>}</p>}

    {record && <>
      {localImageName(record) && <SourceImage key={record.record_id} caseId={caseId} recordId={record.record_id} name={localImageName(record)!} />}
      {/* A remote-only attachment may have a local copy linked to this record in the
          evidence manifest (record_ids); the backend decides. Its URL is never loaded. */}
      {!localImageName(record) && (() => {
        const attachments = remoteImageAttachments(record);
        const single = attachments.length === 1;
        return attachments.map((attachment, index) => {
          const offline = <p key={index} className="blanc-source-offline">
            Attached image <strong>{attachment.filename}</strong> is stored on {attachment.host}, not in the local evidence, so it is not shown. Blanc never loads evidence from the network.
          </p>;
          return index === 0
            ? <SourceImage key={`${record.record_id}-attachment`} caseId={caseId} recordId={record.record_id}
                name={single ? attachment.filename : "attachment"}
                caption={single
                  ? `Local copy of the attachment ${attachment.filename}, linked to this message in the evidence manifest`
                  : "Local copy of an attachment linked to this message in the evidence manifest"}
                missing={offline} />
            : offline;
        });
      })()}
      {view?.message !== undefined && <blockquote className="blanc-source-message">{view.message || "(Empty message)"}</blockquote>}
      {view ? <dl className="blanc-source-fields">
        {view.fields.map((field, index) => <div key={index}>
          <dt>{field.label}{field.derived && <span className="blanc-source-derived" title="Read from the URL above, not stored separately"> · from URL</span>}</dt>
          <dd>{field.value}</dd>
          {field.link && <dd className="blanc-source-link">
            <a href={field.link} target="_blank" rel="noopener noreferrer">{linkLabel(field.link)} ↗</a>
            <small>Opens the live site in a new tab, outside Blanc.</small>
          </dd>}
        </div>)}
      </dl> : <pre className="blanc-source-raw">{record.content}</pre>}

      <details className="blanc-source-details" open>
        <summary>Provenance</summary>
        <dl className="blanc-source-fields">
          <div><dt>Original file</dt><dd>{record.source_file}</dd></div>
          <div><dt>Location in file</dt><dd>{record.source_locator}</dd></div>
          <div><dt>Source time</dt><dd>{record.timestamp ?? "Not recorded"}{record.timestamp_kind ? ` · ${record.timestamp_kind}` : ""}</dd></div>
          <div><dt>Record ID</dt><dd className="blanc-mono">{record.record_id}</dd></div>
        </dl>
      </details>
      {view && <details className="blanc-source-details">
        <summary>Raw record</summary>
        <pre className="blanc-source-raw">{record.content}</pre>
      </details>}
      {view && <p className="blanc-source-note">Readable fields are extracted from the original record; the raw record is unchanged.</p>}
    </>}
    {(onExclude || onRestore) && <div className="blanc-source-actions">
      {excluded
        ? <button type="button" onClick={onRestore} disabled={busy || !onRestore}>Restore this record</button>
        : <button type="button" onClick={onExclude} disabled={busy || !onExclude}>Exclude from this analysis</button>}
      <small>{busy ? "Saving a new revision…" : "The record stays in the case. This saves a new revision and leaves the current one in place."}</small>
    </div>}
  </aside>;
}

type ImageState =
  | { status: "loading" }
  | { status: "ready"; src: string; hashVerified: boolean }
  | { status: "missing" }
  | { status: "integrity" };

/** The record's original image from the local evidence folder, with a larger view on
 *  click. Fetches the media bytes so 404 (missing) and 409 (integrity) can be told apart,
 *  and so ``X-Hash-Verified`` can shape the caption. Revokes the blob URL on cleanup. */
function SourceImage({ caseId, recordId, name, caption, missing }: {
  caseId: string; recordId: string; name: string;
  /** Overrides the default caption, e.g. for a linked local copy of an attachment. */
  caption?: string;
  /** What to show when no local image is available, instead of the default note. */
  missing?: ReactNode;
}) {
  const [state, setState] = useState<ImageState>({ status: "loading" });
  const viewer = useRef<HTMLDialogElement>(null);

  useEffect(() => {
    // Initial state is already "loading"; the panel keys SourceImage by record, so do not
    // setState("loading") here (react-hooks/set-state-in-effect).
    const controller = new AbortController();
    let objectUrl: string | undefined;
    fetch(mediaUrl(caseId, recordId), { signal: controller.signal, credentials: "include" })
      .then(async response => {
        if (controller.signal.aborted) return;
        const interpreted = interpretMediaResponse(response.status, response.headers);
        if (interpreted.status !== "ready") {
          setState(interpreted);
          return;
        }
        const blob = await response.blob();
        if (controller.signal.aborted) return;
        objectUrl = URL.createObjectURL(blob);
        setState({ status: "ready", src: objectUrl, hashVerified: interpreted.hashVerified });
      })
      .catch(() => {
        if (controller.signal.aborted) return;
        setState({ status: "missing" });
      });
    return () => {
      controller.abort();
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [caseId, recordId]);

  if (state.status === "missing") {
    return missing ?? <p className="blanc-source-offline">The original image <strong>{name}</strong> is not in the local evidence folder.</p>;
  }
  if (state.status === "integrity") {
    return <p className="blanc-source-offline" role="alert">The local file for <strong>{name}</strong> failed its integrity check, so it is not shown as evidence.</p>;
  }
  const ready = state.status === "ready";
  const defaultCaption = ready ? mediaCaption(name, state.hashVerified) : `${name} · original file from local evidence`;
  return <figure className="blanc-source-media" data-state={state.status}>
    <button type="button" onClick={() => viewer.current?.showModal()} aria-label={`View ${name} larger`} disabled={!ready}>
      {/* eslint-disable-next-line @next/next/no-img-element -- local evidence served by the case API, not a static asset */}
      {ready && <img src={state.src} alt={`Original image ${name} from the evidence`} />}
    </button>
    <figcaption>{caption ?? defaultCaption}{ready ? " · click to enlarge" : ""}</figcaption>
    {ready && <dialog ref={viewer} className="blanc-media-viewer" aria-label={`${name}, full size`} onClick={event => { if (event.target === event.currentTarget) viewer.current?.close(); }}>
      <button type="button" onClick={() => viewer.current?.close()}>Close</button>
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img src={state.src} alt={`Original image ${name}, full size`} />
      <p>{name}</p>
    </dialog>}
  </figure>;
}
