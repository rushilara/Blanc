"use client";

import { useEffect, useId, useMemo, useState } from "react";
import type { EvidenceRecord } from "@/lib/analysis";
import { groupTimeline, normalizeEvents, type NormalizedTimeline, type TimelineEvent, type TimelineItem } from "@/lib/timeline";
import { collectEventTypes, filterTimeline } from "@/lib/timelineFilter";
import { supportChanged, supportLabel, type SupportStatus } from "@/lib/comparison";
import { timelineCaption } from "@/lib/timeline-presentation";
import { presentSource } from "@/lib/source-presentation";
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
  onOpenSource,
  openSourceId,
}: {
  events: TimelineEvent[];
  caseId: string;
  records: EvidenceRecord[];
  excludedRecordIds?: string[];
  onExclude?: (recordId: string) => void;
  onAnnotate?: (recordId: string) => void;
  eventSupport?: Record<string, SupportStatus>;
  /** Open a record in the workspace's source panel (the same one Findings uses).
   *  Without it, sources open in the standalone SourceReferences drawer. */
  onOpenSource?: (recordId: string) => void;
  /** The record currently open in that panel, to mark its button. */
  openSourceId?: string;
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
  if (!events.length) return <p className="blanc-timeline-empty">No supported events in this revision.</p>;
  function group(title: string, items: TimelineItem[], id: string) {
    if (!items.length) return null;
    return <section aria-labelledby={id} className="blanc-timeline-group">
      <h3 id={id}>{title} <span>{items.length}</span></h3>
      {id === "uncertain-events" && <p className="blanc-timeline-note">These events remain separate because their position in the chronology is uncertain.</p>}
      <ol className="blanc-event-list">
        {items.map(({ event, time }, index) => {
          const support = eventSupport?.[event.id];
          const changed = support ? supportChanged(support) : false;
          const caption = timelineCaption(time);
          const sourceCount = new Set(event.evidence_ids).size;
          return <li key={event.id} data-event-id={event.id} data-support={support} className={`blanc-event${changed ? " is-changed" : ""}`}>
            <div className="blanc-event-time"><span>{caption.date}</span><small>{caption.time}</small></div>
            <div className="blanc-event-marker" aria-hidden="true">{id === "dated-events" ? String(index + 1).padStart(2, "0") : "—"}</div>
            <article className="blanc-event-body">
              <div className="blanc-event-meta"><span className={`blanc-status blanc-status-${event.status}`}>{event.status}</span>{support && <span className={`blanc-support blanc-support-${support}`}>{supportLabel(support)}</span>}</div>
              <p className="blanc-event-description">{event.description}</p>
              {onOpenSource
                ? <div className="blanc-event-sources">{[...new Set(event.evidence_ids)].map(id => {
                    const record = records.find(item => item.record_id === id);
                    const name = (record && presentSource(record)?.name) ?? id;
                    return <button key={id} type="button" aria-pressed={openSourceId === id} onClick={() => onOpenSource(id)}>
                      {sourceCount === 1 ? "View source" : name}{excludedRecordIds?.includes(id) ? " · excluded" : ""}
                    </button>;
                  })}</div>
                : <SourceReferences records={records} caseId={caseId} ids={event.evidence_ids}
                  triggerLabel={`View ${sourceCount} source${sourceCount === 1 ? "" : "s"}`}
                  excludedRecordIds={excludedRecordIds} onExclude={onExclude} onAnnotate={onAnnotate} />}
              <details className="blanc-event-details"><summary>Time and provenance</summary>
                <dl><div><dt>Precision</dt><dd>{time ? PRECISION[time.precision] : "Not normalized"}</dd></div>
                  <div><dt>Time source</dt><dd>{(event.timestamp_kind ?? "unknown").replaceAll("_", " ")}</dd></div>
                  <div><dt>Source timestamp</dt><dd>{event.timestamp ?? "Not recorded"}</dd></div>
                  <div><dt>Display timestamp</dt><dd>{time?.display ?? "Not established"}</dd></div>
                  <div><dt>Timezone</dt><dd>{time?.timezone_known ? "Normalized to UTC" : "Not established; no timezone assumed"}</dd></div>
                  <div><dt>Source records</dt><dd>{[...new Set(event.evidence_ids)].join(" · ") || "None cited"}</dd></div></dl>
              </details>
            </article>
          </li>;
        })}
      </ol>
    </section>;
  }
  return <div className="blanc-timeline">
    <div className="blanc-timeline-toolbar">
      <label className="blanc-timeline-search" htmlFor={searchId}><span>Find an event</span>
        <input id={searchId} type="search" value={query} onChange={event => setQuery(event.target.value)} placeholder="Search events…" />
      </label>
      {eventTypeOptions.length > 0 && <fieldset><legend>Time source</legend><div className="blanc-timeline-filters">
        {eventTypeOptions.map(option => { const on = eventTypes.includes(option);return <button key={option} type="button" aria-pressed={on} onClick={() => setEventTypes(on ? eventTypes.filter(value => value !== option) : [...eventTypes, option])}>{option.replaceAll("_", " ")}</button>; })}
      </div></fieldset>}
    </div>
    {filtered.active && <p className="blanc-timeline-note" role="status">Showing {filtered.events.length} of {events.length} events. <button type="button" onClick={() => {setQuery("");setEventTypes([]);}}>Clear filters</button></p>}
    {!normalized && !error && <p role="status" className="blanc-timeline-note">Checking event times…</p>}
    {error && <p role="alert" className="blanc-timeline-note">Event times could not be verified. Events are shown separately without assuming their order. <button type="button" onClick={() => {setFailed(null);setRetry(n => n + 1);}}>Retry</button></p>}
    {normalized && <p className="blanc-timeline-note">Chronological order · UTC where established · source precision preserved</p>}
    {filtered.events.length === 0 ? <p className="blanc-timeline-empty">No events match the current filters.</p> : <>
      {group("Sequence of events", groups.dated, "dated-events")}
      {group("Time uncertain", groups.uncertain, "uncertain-events")}
    </>}
  </div>;
}
