"use client";

import type { AnalysisRevision } from "@/lib/analysis";
import type { OnSelectEvidence } from "@/lib/evidence-selection";
import {
  normalizeTimeline,
  type TimelineEntry,
  type TimestampPrecision,
} from "@/lib/timeline";

export function TimelineView({
  events,
  onSelect,
}: {
  events: AnalysisRevision["timeline_events"];
  onSelect?: OnSelectEvidence;
}) {
  const { dated, undated } = normalizeTimeline(events);

  if (!events.length) {
    return <p className="text-sm text-zinc-500">No supported events.</p>;
  }

  return (
    <div className="space-y-6">
      {dated.length > 0 && (
        <ol aria-label="Dated timeline events" className="relative space-y-0">
          <div
            aria-hidden
            className="absolute top-2 bottom-2 left-[0.4375rem] w-px bg-zinc-200 dark:bg-zinc-700"
          />
          {dated.map((entry) => (
            <TimelineItem
              key={entry.event.id}
              entry={entry}
              onSelect={onSelect}
              showRail
            />
          ))}
        </ol>
      )}

      {undated.length > 0 && (
        <section aria-labelledby="undated-events-heading" className="space-y-3">
          <h3
            id="undated-events-heading"
            className="text-sm font-semibold text-zinc-700 dark:text-zinc-300"
          >
            Undated or uncertain
          </h3>
          <p className="text-xs text-zinc-500">
            These events have no reliable timestamp, so they are kept off the chronological
            sequence.
          </p>
          <ol aria-label="Undated or uncertain events" className="space-y-0">
            {undated.map((entry) => (
              <TimelineItem
                key={entry.event.id}
                entry={entry}
                onSelect={onSelect}
                showRail={false}
              />
            ))}
          </ol>
        </section>
      )}

      {!dated.length && !undated.length && (
        <p className="text-sm text-zinc-500">No supported events.</p>
      )}
    </div>
  );
}

function TimelineItem({
  entry,
  onSelect,
  showRail,
}: {
  entry: TimelineEntry;
  onSelect?: OnSelectEvidence;
  showRail: boolean;
}) {
  const { event, time } = entry;
  const selectable = typeof onSelect === "function";
  const label = time.display ?? "Undated";

  const activate = () => {
    onSelect?.({
      kind: "event",
      id: event.id,
      label: event.description,
      evidenceIds: event.evidence_ids,
    });
  };

  const sharedClass = [
    "relative w-full rounded-lg px-3 py-3 text-left transition-colors",
    showRail ? "pl-7" : "",
    selectable
      ? "cursor-pointer hover:bg-zinc-50 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-zinc-400 dark:hover:bg-zinc-900 dark:focus-visible:outline-zinc-500"
      : "",
  ]
    .filter(Boolean)
    .join(" ");

  const body = (
    <>
      {showRail && (
        <span
          aria-hidden
          className="absolute top-4 left-0 h-2.5 w-2.5 rounded-full border-2 border-zinc-400 bg-white dark:border-zinc-500 dark:bg-zinc-950"
        />
      )}
      <div className="space-y-2">
        <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
          <time
            className="break-words font-mono text-xs text-zinc-700 dark:text-zinc-300"
            dateTime={time.display ?? undefined}
          >
            {label}
          </time>
          {!time.timezoneKnown && time.instantMs !== null && time.precision === "exact" && (
            <span className="text-[0.65rem] uppercase tracking-wide text-amber-700 dark:text-amber-400">
              zone unknown
            </span>
          )}
        </div>
        <div className="flex flex-wrap gap-1.5">
          <Badge label={precisionLabel(time.precision)} tone={precisionTone(time.precision)} />
          <Badge
            label={(event.timestamp_kind ?? "unknown").replaceAll("_", " ")}
            tone="neutral"
          />
          <Badge label={event.status.replaceAll("_", " ")} tone="neutral" />
        </div>
        <p className="text-sm leading-6">{event.description}</p>
        <SourceRefs ids={event.evidence_ids} />
      </div>
    </>
  );

  if (!selectable) {
    return <li className={sharedClass}>{body}</li>;
  }

  return (
    <li>
      <button
        type="button"
        className={sharedClass}
        onClick={activate}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            activate();
          }
        }}
        aria-label={`Open evidence for event: ${event.description}`}
      >
        {body}
      </button>
    </li>
  );
}

function Badge({
  label,
  tone,
}: {
  label: string;
  tone: "neutral" | "warn" | "muted";
}) {
  const tones = {
    neutral:
      "border-zinc-200 bg-zinc-50 text-zinc-600 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-400",
    warn: "border-amber-300 bg-amber-50 text-amber-900 dark:border-amber-800 dark:bg-amber-950 dark:text-amber-200",
    muted:
      "border-zinc-200 bg-transparent text-zinc-500 dark:border-zinc-700 dark:text-zinc-400",
  };
  return (
    <span
      className={`inline-flex rounded border px-1.5 py-0.5 text-[0.65rem] font-medium capitalize ${tones[tone]}`}
    >
      {label}
    </span>
  );
}

function precisionLabel(precision: TimestampPrecision): string {
  if (precision === "exact") return "exact";
  if (precision === "day") return "day precision";
  if (precision === "month") return "month precision";
  if (precision === "approximate") return "approximate";
  return "uncertain";
}

function precisionTone(precision: TimestampPrecision): "neutral" | "warn" | "muted" {
  if (precision === "exact") return "muted";
  if (precision === "unknown") return "warn";
  return "warn";
}

function SourceRefs({ ids }: { ids: string[] }) {
  return (
    <ul aria-label="Source record references" className="flex flex-wrap gap-1.5">
      {[...new Set(ids)].map((id) => (
        <li
          key={id}
          className="rounded border border-zinc-200 px-2 py-0.5 font-mono text-xs text-zinc-600 dark:border-zinc-700 dark:text-zinc-400"
        >
          {id}
        </li>
      ))}
    </ul>
  );
}
