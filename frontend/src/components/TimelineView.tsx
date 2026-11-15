"use client";

import { useEffect, useId, useMemo, useState } from "react";
import type { EvidenceRecord } from "@/lib/analysis";
import { groupTimeline, normalizeEvents, type NormalizedTimeline, type TimelineEvent, type TimelineItem } from "@/lib/timeline";
import { collectEventTypes, filterTimeline } from "@/lib/timelineFilter";
import { supportChanged, supportLabel, type SupportStatus } from "@/lib/comparison";
import { SourceReferences } from "./SourceReferences";

const PRECISION = { exact: "Exact time", day: "Day only", month: "Month only", approximate: "Approximate", unknown: "Undated / uncertain" };

export function TimelineView({
  events,
  caseId,
  records,
  excludedRecordIds,
  onExclude,
  onAnnotate,
  eventSupport,
}: {
  events: TimelineEvent[];
  caseId: string;
  records: EvidenceRecord[];
  excludedRecordIds?: string[];
  onExclude?: (recordId: string) => void;
  onAnnotate?: (recordId: string) => void;
  eventSupport?: Record<string, SupportStatus>;
}) {
  const [result, setResult] = useState<{ input: TimelineEvent[]; data: NormalizedTimeline } | null>(null);
  const [failed, setFailed] = useState<TimelineEvent[] | null>(null);
  const [retry, setRetry] = useState(0);
  const [query, setQuery] = useState("");
  const [eventTypes, setEventTypes] = useState<string[]>([]);
  const searchId = useId();
  const eventTypeOptions = useMemo(() => collectEventTypes(events), [events]);
  const filtered = useMemo(() => filterTimeline(events, { query, eventTypes }), [events, query, eventTypes]);

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
  const groups = groupTimeline(filtered.events, normalized ? {
    dated: normalized.dated.filter((entry) => filtered.events.some((event) => event.id === entry.event_id)),
    undated: normalized.undated.filter((entry) => filtered.events.some((event) => event.id === entry.event_id)),
  } : null);
  if (!events.length) return <p className="text-sm text-zinc-500">No supported events in this revision.</p>;
  function group(title: string, items: TimelineItem[], id: string) {
    return <section aria-labelledby={id} className="space-y-3">
      <h3 id={id} className="text-sm font-semibold">{title} <span className="font-normal text-zinc-500">({items.length})</span></h3>
      {!items.length && <p className="text-xs text-zinc-500">{id === "dated-events" ? "No events with a sufficiently established date." : "No undated or uncertain events."}</p>}
      <ol className="space-y-4">
        {items.map(({ event, time }) => {
          const support = eventSupport?.[event.id];
          const changed = support ? supportChanged(support) : false;
          return <li key={event.id} data-event-id={event.id} data-support={support} className={`space-y-2 border-l-2 pl-3 ${changed ? "is-changed border-[#8a5a12]" : "border-zinc-300 dark:border-zinc-700"}`}>
          <p className="break-words font-mono text-xs">{time?.display ?? "Time not established"}</p>
          {support && <p className={`blanc-support blanc-support-${support}`}>{supportLabel(support)}</p>}
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
        </li>;
        })}
      </ol>
    </section>;
  }
  return <div className="space-y-6">
    <div className="space-y-3 rounded-lg border border-zinc-200 p-3 dark:border-zinc-800">
      <div className="space-y-1">
        <label htmlFor={searchId} className="text-xs font-medium text-zinc-600 dark:text-zinc-300">
          Search timeline
        </label>
        <input
          id={searchId}
          type="search"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder="Description, kind, or status…"
          className="w-full rounded border border-zinc-300 bg-white px-2 py-1.5 text-sm dark:border-zinc-700 dark:bg-zinc-950"
        />
      </div>
      {eventTypeOptions.length > 0 && (
        <fieldset className="space-y-1.5">
          <legend className="text-xs font-medium text-zinc-600 dark:text-zinc-300">Time source</legend>
          <div className="flex flex-wrap gap-1.5">
            {eventTypeOptions.map((option) => {
              const on = eventTypes.includes(option);
              return (
                <button
                  key={option}
                  type="button"
                  aria-pressed={on}
                  onClick={() =>
                    setEventTypes(on ? eventTypes.filter((value) => value !== option) : [...eventTypes, option])
                  }
                  className={`rounded border px-2 py-0.5 text-xs capitalize ${
                    on
                      ? "border-blue-400 bg-blue-50 text-blue-900 dark:border-blue-700 dark:bg-blue-950 dark:text-blue-100"
                      : "border-zinc-300 text-zinc-600 dark:border-zinc-700 dark:text-zinc-300"
                  }`}
                >
                  {option.replaceAll("_", " ")}
                </button>
              );
            })}
          </div>
        </fieldset>
      )}
      {filtered.active && (
        <p className="text-xs text-zinc-500" role="status">
          Showing {filtered.events.length} of {events.length} events.
          <button
            type="button"
            className="ml-2 underline"
            onClick={() => {
              setQuery("");
              setEventTypes([]);
            }}
          >
            Clear filters
          </button>
        </p>
      )}
    </div>
    {!normalized && !error && <p role="status" className="text-xs text-zinc-500">Normalizing source timestamps…</p>}
    {error && <p role="alert" className="text-xs text-amber-800 dark:text-amber-300">Could not verify event times. Source text is shown without chronological placement. <button type="button" onClick={() => { setFailed(null); setRetry(n => n + 1); }} className="underline">Retry</button></p>}
    {normalized && <p className="text-xs text-zinc-500">Ordered by backend-normalized time. Day and month entries retain their source precision.</p>}
    {filtered.events.length === 0 ? (
      <p className="text-sm text-zinc-500">No events match the current filters.</p>
    ) : (
      <>
        {group("Dated events", groups.dated, "dated-events")}
        {group("Undated or uncertain", groups.uncertain, "uncertain-events")}
      </>
    )}
  </div>;
}
