import type { TimelineEvent } from "@/lib/timeline";

export type TimelineFilterState = {
  query: string;
  eventTypes: string[];
};

export type FilteredTimeline = {
  events: TimelineEvent[];
  active: boolean;
};

function normalize(value: string | undefined): string {
  return (value ?? "").trim().toLowerCase();
}

function hasQuery(query: string): boolean {
  return normalize(query).length > 0;
}

function matchesQuery(haystack: string | undefined, query: string): boolean {
  const needle = normalize(query);
  return !needle || normalize(haystack).includes(needle);
}

/** Distinct event kinds (timestamp_kind) for filter controls. */
export function collectEventTypes(events: TimelineEvent[]): string[] {
  return [...new Set(events.map((event) => event.timestamp_kind ?? "unknown"))].sort((a, b) =>
    a.localeCompare(b),
  );
}

export function timelineFiltersActive(filters: TimelineFilterState): boolean {
  return hasQuery(filters.query) || filters.eventTypes.length > 0;
}

/** Search timeline text and filter by event type (timestamp_kind). */
export function filterTimeline(events: TimelineEvent[], filters: TimelineFilterState): FilteredTimeline {
  const active = timelineFiltersActive(filters);
  if (!active) return { events, active: false };

  const types = new Set(filters.eventTypes);
  const filtered = events.filter((event) => {
    const kind = event.timestamp_kind ?? "unknown";
    const typeOk = types.size === 0 || types.has(kind);
    const queryOk =
      !hasQuery(filters.query) ||
      matchesQuery(event.description, filters.query) ||
      matchesQuery(event.id, filters.query) ||
      matchesQuery(event.timestamp ?? undefined, filters.query) ||
      matchesQuery(kind, filters.query) ||
      matchesQuery(event.status, filters.query);
    return typeOk && queryOk;
  });
  return { events: filtered, active: true };
}
