import { API_BASE_URL } from "./api";
import type { AnalysisRevision } from "./analysis";

export type TimelineEvent = AnalysisRevision["timeline_events"][number];
export type NormalizedEntry = {
  event_id: string; instant: string | null;
  precision: "exact" | "day" | "month" | "approximate" | "unknown";
  timezone_known: boolean; display: string | null;
};
export type NormalizedTimeline = { dated: NormalizedEntry[]; undated: NormalizedEntry[] };
export type TimelineItem = { event: TimelineEvent; time: NormalizedEntry | null };

export function parseTimeline(data: unknown, events: TimelineEvent[]): NormalizedTimeline {
  const fail = () => { throw new Error("The normalized timeline response is invalid."); };
  if (!data || typeof data !== "object" || !("dated" in data) || !("undated" in data)
    || !Array.isArray(data.dated) || !Array.isArray(data.undated)) return fail();
  const expected = new Set(events.map(event => event.id));
  const seen = new Set<string>();
  for (const [group, entries] of [["dated", data.dated], ["undated", data.undated]] as const) {
    for (const entry of entries) {
      if (!entry || typeof entry !== "object" || typeof entry.event_id !== "string"
        || !expected.has(entry.event_id) || seen.has(entry.event_id)
        || !["exact", "day", "month", "approximate", "unknown"].includes(entry.precision)
        || typeof entry.timezone_known !== "boolean") return fail();
      if (group === "dated") {
        if (typeof entry.instant !== "string" || !entry.instant.endsWith("Z")
          || typeof entry.display !== "string" || !entry.display || entry.precision === "unknown") return fail();
      } else if (entry.instant !== null || entry.display !== null || entry.precision !== "unknown") return fail();
      seen.add(entry.event_id);
    }
  }
  if (seen.size !== events.length || expected.size !== events.length) return fail();
  return data as NormalizedTimeline;
}

export async function normalizeEvents(events: TimelineEvent[], signal?: AbortSignal): Promise<NormalizedTimeline> {
  const response = await fetch(`${API_BASE_URL}/timeline/normalize`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ events }), cache: "no-store", signal,
  });
  if (!response.ok) throw new Error("Timestamp normalization is unavailable.");
  return parseTimeline(await response.json(), events);
}

export function groupTimeline(events: TimelineEvent[], normalized: NormalizedTimeline | null): { dated: TimelineItem[]; uncertain: TimelineItem[] } {
  if (!normalized) return { dated: [], uncertain: events.map(event => ({ event, time: null })) };
  const byId = new Map(events.map(event => [event.id, event]));
  const dated: TimelineItem[] = [], uncertain: TimelineItem[] = [];
  // Preserve the backend's total order. Never parse a source date with JS Date,
  // which would silently assign a local timezone to zone-less timestamps.
  for (const time of normalized.dated) {
    const item = { event: byId.get(time.event_id)!, time };
    if (time.precision === "approximate" || (time.precision === "exact" && !time.timezone_known)) uncertain.push(item);
    else dated.push(item);
  }
  for (const time of normalized.undated) uncertain.push({ event: byId.get(time.event_id)!, time });
  return { dated, uncertain };
}
