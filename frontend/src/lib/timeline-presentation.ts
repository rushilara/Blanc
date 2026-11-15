import type { NormalizedEntry } from "./timeline";

/** Format only the backend's normalized UTC instant, never a raw source timestamp. */
export function timelineCaption(entry: NormalizedEntry | null): { date: string; time: string } {
  if (!entry?.instant || entry.precision === "approximate" || (entry.precision === "exact" && !entry.timezone_known)) {
    return { date: entry?.display ?? "Time not established", time: "Chronology uncertain" };
  }
  const instant = new Date(entry.instant);
  if (!Number.isFinite(instant.getTime())) return { date: entry.display ?? "Time not established", time: "" };
  const options: Intl.DateTimeFormatOptions = entry.precision === "month"
    ? { month: "short", year: "numeric", timeZone: "UTC" }
    : { day: "numeric", month: "short", year: "numeric", timeZone: "UTC" };
  const time = entry.precision === "exact"
    ? `${entry.instant.split("T")[1]?.replace(/Z$/, "") ?? ""} UTC`
    : entry.precision === "day" ? "Day precision" : "Month precision";
  return { date: instant.toLocaleDateString("en-US", options), time };
}
