"use client";

import { useEffect, useState } from "react";
import type { EvidenceRecord } from "@/lib/analysis";
import { groupTimeline, normalizeEvents, type NormalizedTimeline, type TimelineEvent, type TimelineItem } from "@/lib/timeline";
import { SourceReferences } from "./SourceReferences";

const PRECISION = { exact: "Exact time", day: "Day only", month: "Month only", approximate: "Approximate", unknown: "Undated / uncertain" };

export function TimelineView({
  events,
  caseId,
  records,
  excludedRecordIds,
  onExclude,
  onAnnotate,
}: {
  events: TimelineEvent[];
  caseId: string;
  records: EvidenceRecord[];
  excludedRecordIds?: string[];
  onExclude?: (recordId: string) => void;
  onAnnotate?: (recordId: string) => void;
}) {
  const [result, setResult] = useState<{ input: TimelineEvent[]; data: NormalizedTimeline } | null>(null);
  const [failed, setFailed] = useState<TimelineEvent[] | null>(null);
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    if (!events.length) return;
    const controller = new AbortController();
    normalizeEvents(events, controller.signal).then(data => {
      if (!controller.signal.aborted) setResult({ input: events, data });
    }).catch(() => { if (!controller.signal.aborted) setFailed(events); });
    return () => controller.abort();
  }, [events, caseId, retry]);
  const normalized = result?.input === events ? result.data : null;
  const error = !normalized && failed === events;
  const groups = groupTimeline(events, normalized);
  if (!events.length) return <p className="text-sm text-zinc-500">No supported events in this revision.</p>;
  function group(title: string, items: TimelineItem[], id: string) {
    return <section aria-labelledby={id} className="space-y-3">
      <h3 id={id} className="text-sm font-semibold">{title} <span className="font-normal text-zinc-500">({items.length})</span></h3>
      {!items.length && <p className="text-xs text-zinc-500">{id === "dated-events" ? "No events with a sufficiently established date." : "No undated or uncertain events."}</p>}
      <ol className="space-y-4">
        {items.map(({ event, time }) => <li key={event.id} data-event-id={event.id} className="space-y-2 border-l-2 border-zinc-300 pl-3 dark:border-zinc-700">
          <p className="break-words font-mono text-xs">{time?.display ?? "Time not established"}</p>
          <div className="flex flex-wrap gap-1.5 text-xs">
            <span className="rounded border border-zinc-300 px-2 py-0.5 dark:border-zinc-700">{time ? PRECISION[time.precision] : "Not normalized"}</span>
            <span className="rounded border border-zinc-300 px-2 py-0.5 dark:border-zinc-700">{(event.timestamp_kind ?? "unknown").replaceAll("_", " ")}</span>
            <span className="rounded border border-zinc-300 px-2 py-0.5 capitalize dark:border-zinc-700">{event.status}</span>
          </div>
          {time?.precision === "exact" && !time.timezone_known && <p className="text-xs text-amber-700 dark:text-amber-300">Time zone unknown — not placed on the absolute timeline.</p>}
          {time?.timezone_known && <p className="text-xs text-zinc-500">Normalized to UTC</p>}
          {event.timestamp && <p className="break-words text-xs text-zinc-500">Source time as written: {event.timestamp}</p>}
          <SourceReferences
            records={records}
            caseId={caseId}
            ids={event.evidence_ids}
            triggerLabel={event.description}
            excludedRecordIds={excludedRecordIds}
            onExclude={onExclude}
            onAnnotate={onAnnotate}
          />
          <p className="break-all font-mono text-xs text-zinc-500">{[...new Set(event.evidence_ids)].join(" · ")}</p>
        </li>)}
      </ol>
    </section>;
  }
  return <div className="space-y-6">
    {!normalized && !error && <p role="status" className="text-xs text-zinc-500">Normalizing source timestamps…</p>}
    {error && <p role="alert" className="text-xs text-amber-800 dark:text-amber-300">Could not verify event times. Source text is shown without chronological placement. <button type="button" onClick={() => { setFailed(null); setRetry(n => n + 1); }} className="underline">Retry</button></p>}
    {normalized && <p className="text-xs text-zinc-500">Ordered by backend-normalized time. Day and month entries retain their source precision.</p>}
    {group("Dated events", groups.dated, "dated-events")}
    {group("Undated or uncertain", groups.uncertain, "uncertain-events")}
  </div>;
}
