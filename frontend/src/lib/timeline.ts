/**
 * Deterministic timeline normalization for the UI (mirrors backend/app/timeline.py, BLA-19).
 *
 * The LLM writes timeline events; this module decides what their timestamps mean and in
 * what order they go. Same inputs always produce the same dated / undated split.
 *
 * Rules (aligned with the backend):
 * - Never invent precision. Declared precision may only coarsen what was written.
 * - Known zones (numeric offset, Z/UTC/GMT, or IANA name) convert to UTC for ordering.
 * - No zone → keep wall-clock as written, flag timezoneKnown=false, order as written.
 * - Ambiguous / unparseable text, missing timestamps, or declared precision "unknown"
 *   go to the undated group.
 * - Order: instant, then finer precision first, then event id and description.
 *
 * Known gap vs Python: DST gap/overlap detection uses Intl round-trips rather than
 * ZoneInfo fold=0/1. Offsets and unambiguous named zones match; edge-case DST days
 * should still land in precision "day" when the wall time does not round-trip cleanly.
 */

import type { AnalysisRevision } from "@/lib/analysis";

export type TimestampPrecision = "exact" | "day" | "month" | "approximate" | "unknown";

export type TimelineEvent = AnalysisRevision["timeline_events"][number];

export type NormalizedTime = {
  /** UTC epoch ms used for ordering; null when undated. */
  instantMs: number | null;
  precision: TimestampPrecision;
  timezoneKnown: boolean;
  /** Display text at the precision actually known; null when undated. */
  display: string | null;
};

export type TimelineEntry = {
  event: TimelineEvent;
  time: NormalizedTime;
};

export type NormalizedTimeline = {
  dated: TimelineEntry[];
  undated: TimelineEntry[];
};

const PRECISION_RANK: Record<Exclude<TimestampPrecision, "unknown">, number> = {
  exact: 0,
  day: 1,
  month: 2,
  approximate: 3,
};

// Numbered groups (tsconfig targets ES2017; named groups need ES2018+).
// ISO: 1 year, 2 month, 3 day, 4 hour, 5 minute, 6 second, 7 fraction, 8 offset, 9 zone
const ISO =
  /^(\d{4})(?:-(\d{2})(?:-(\d{2})(?:[T ](\d{2}):(\d{2})(?::(\d{2})(?:[.,](\d{1,9}))?)?\s*(Z|UTC|GMT|[+-]\d{2}(?::?\d{2})?)?(?:\s*\[?([A-Za-z_]+(?:\/[A-Za-z0-9_+\-]+)+)\]?)?)?)?)?$/i;

// EPOCH: 1 whole, 2 fraction
const EPOCH = /^(\d{10}|\d{13})(?:\.(\d+))?$/;
// COMPACT: 1 year, 2 month, 3 day, 4 hour
const COMPACT_DATE_HOUR = /^(19\d{2}|20\d{2}|2100)(\d{2})(\d{2})(\d{2})$/;
const EPOCH_YEAR_MIN = 1990;
const EPOCH_YEAR_MAX = 2100;
const MAX_OFFSET_MS = 14 * 60 * 60 * 1000;

export const UNDATED: NormalizedTime = {
  instantMs: null,
  precision: "unknown",
  timezoneKnown: false,
  display: null,
};

export function normalizeTimeline(events: readonly TimelineEvent[]): NormalizedTimeline {
  const entries = events.map((event) => ({
    event,
    time: normalizeTimestamp(event.timestamp, event.timestamp_precision),
  }));

  const dated = entries
    .filter((item) => item.time.instantMs !== null)
    .sort((a, b) => {
      const byInstant = (a.time.instantMs as number) - (b.time.instantMs as number);
      if (byInstant !== 0) return byInstant;
      const byPrecision =
        PRECISION_RANK[a.time.precision as Exclude<TimestampPrecision, "unknown">] -
        PRECISION_RANK[b.time.precision as Exclude<TimestampPrecision, "unknown">];
      if (byPrecision !== 0) return byPrecision;
      const byId = a.event.id.localeCompare(b.event.id);
      if (byId !== 0) return byId;
      return a.event.description.localeCompare(b.event.description);
    });

  const undated = entries
    .filter((item) => item.time.instantMs === null)
    .sort((a, b) => {
      const byId = a.event.id.localeCompare(b.event.id);
      if (byId !== 0) return byId;
      return a.event.description.localeCompare(b.event.description);
    });

  return { dated, undated };
}

export function normalizeTimestamp(
  raw: string | null | undefined,
  declared?: TimestampPrecision | null,
): NormalizedTime {
  if (raw == null || declared === "unknown") return UNDATED;
  const text = raw.trim();
  if (!text) return UNDATED;

  const parsed = parseEpoch(text) ?? parseIso(text);
  if (!parsed) return UNDATED;

  const { written, timezoneKnown } = parsed;
  let { instantMs } = parsed;
  let precision: TimestampPrecision = written;
  // declared "unknown" already returned above; remaining values may only coarsen.
  if (declared != null && PRECISION_RANK[declared] > PRECISION_RANK[written]) {
    precision = declared;
  }
  instantMs = truncate(instantMs, precision, written);
  return {
    instantMs,
    precision,
    timezoneKnown,
    display: formatDisplay(instantMs, precision, written, timezoneKnown),
  };
}

type Parsed = {
  instantMs: number;
  written: Exclude<TimestampPrecision, "unknown">;
  timezoneKnown: boolean;
};

function parseEpoch(text: string): Parsed | null {
  const match = EPOCH.exec(text);
  if (!match) return null;
  const whole = match[1];
  if (looksLikeCompactDate(whole)) return null;

  let seconds: number;
  if (whole.length === 10) {
    seconds = match[2] ? Number(`${whole}.${match[2]}`) : Number(whole);
  } else {
    seconds = Number(whole) / 1000;
  }
  if (!Number.isFinite(seconds)) return null;

  const instantMs = Math.trunc(seconds * 1000);
  const year = new Date(instantMs).getUTCFullYear();
  if (year < EPOCH_YEAR_MIN || year > EPOCH_YEAR_MAX) return null;
  return { instantMs, written: "exact", timezoneKnown: true };
}

function looksLikeCompactDate(digits: string): boolean {
  const match = COMPACT_DATE_HOUR.exec(digits);
  if (!match) return false;
  const year = Number(match[1]);
  const month = Number(match[2]);
  const day = Number(match[3]);
  const hour = Number(match[4]);
  if (month < 1 || month > 12 || day < 1 || day > 31 || hour > 23) return false;
  const probe = new Date(Date.UTC(year, month - 1, day, hour));
  return (
    probe.getUTCFullYear() === year &&
    probe.getUTCMonth() === month - 1 &&
    probe.getUTCDate() === day &&
    probe.getUTCHours() === hour
  );
}

function parseIso(text: string): Parsed | null {
  const match = ISO.exec(text);
  if (!match) return null;
  const year = Number(match[1]);
  const month = match[2] ? Number(match[2]) : 1;
  const day = match[3] ? Number(match[3]) : 1;

  if (match[4] == null) {
    const written: Exclude<TimestampPrecision, "unknown"> = match[3]
      ? "day"
      : match[2]
        ? "month"
        : "approximate";
    if (!isValidUtcDate(year, month, day, 0, 0, 0)) return null;
    return {
      instantMs: Date.UTC(year, month - 1, day),
      written,
      timezoneKnown: false,
    };
  }

  const hour = Number(match[4]);
  const minute = Number(match[5]);
  const second = Number(match[6] ?? "0");
  const fractionRaw = match[7];
  const fraction = (fractionRaw ?? "").slice(0, 6).padEnd(6, "0");
  const micros = fractionRaw ? Number(fraction) : 0;
  const millis = Math.trunc(micros / 1000);
  if (!isValidUtcDate(year, month, day, hour, minute, second)) return null;

  const zone = resolveZone(match[8], match[9]);
  // Offset 0 (Z/UTC/GMT) is a valid known zone — do not treat it as missing.
  if (zone === null) {
    return {
      instantMs: Date.UTC(year, month - 1, day, hour, minute, second, millis),
      written: "exact",
      timezoneKnown: false,
    };
  }

  if (typeof zone === "number") {
    const instantMs =
      Date.UTC(year, month - 1, day, hour, minute, second, millis) - zone;
    return { instantMs, written: "exact", timezoneKnown: true };
  }

  const zoned = zonedWallToUtc(year, month, day, hour, minute, second, millis, zone);
  if (!zoned) {
    return {
      instantMs: Date.UTC(year, month - 1, day, hour, minute, second, millis),
      written: "exact",
      timezoneKnown: false,
    };
  }
  if (zoned.ambiguous) {
    return {
      instantMs: Date.UTC(year, month - 1, day),
      written: "day",
      timezoneKnown: true,
    };
  }
  return { instantMs: zoned.instantMs, written: "exact", timezoneKnown: true };
}

function resolveZone(
  offset: string | undefined,
  name: string | undefined,
): number | string | null {
  const fixed = offset ? parseOffsetMs(offset) : null;
  if (fixed !== null) return fixed;
  if (name && isValidTimeZone(name)) return name;
  return null;
}

function parseOffsetMs(offset: string): number | null {
  const upper = offset.toUpperCase();
  if (upper === "Z" || upper === "UTC" || upper === "GMT") return 0;
  const sign = offset[0] === "-" ? -1 : 1;
  const digits = offset.slice(1).replace(":", "");
  const hours = Number(digits.slice(0, 2));
  const minutes = Number(digits.slice(2) || "0");
  if (!Number.isFinite(hours) || !Number.isFinite(minutes) || minutes > 59) return null;
  const delta = (hours * 60 + minutes) * 60 * 1000;
  if (delta > MAX_OFFSET_MS) return null;
  return sign * delta;
}

function isValidTimeZone(name: string): boolean {
  try {
    Intl.DateTimeFormat(undefined, { timeZone: name });
    return true;
  } catch {
    return false;
  }
}

function isValidUtcDate(
  year: number,
  month: number,
  day: number,
  hour: number,
  minute: number,
  second: number,
): boolean {
  const d = new Date(Date.UTC(year, month - 1, day, hour, minute, second));
  return (
    d.getUTCFullYear() === year &&
    d.getUTCMonth() === month - 1 &&
    d.getUTCDate() === day &&
    d.getUTCHours() === hour &&
    d.getUTCMinutes() === minute &&
    d.getUTCSeconds() === second
  );
}

/** Offset of `timeZone` at `utcMs`, in milliseconds east of UTC (JS-style). */
function getTimeZoneOffsetMs(timeZone: string, utcMs: number): number {
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone,
    hourCycle: "h23",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  }).formatToParts(new Date(utcMs));
  const get = (type: Intl.DateTimeFormatPartTypes) =>
    Number(parts.find((p) => p.type === type)?.value);
  const asUtc = Date.UTC(
    get("year"),
    get("month") - 1,
    get("day"),
    get("hour"),
    get("minute"),
    get("second"),
  );
  return asUtc - utcMs;
}

function wallPartsInZone(utcMs: number, timeZone: string) {
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone,
    hourCycle: "h23",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  }).formatToParts(new Date(utcMs));
  const get = (type: Intl.DateTimeFormatPartTypes) =>
    Number(parts.find((p) => p.type === type)?.value);
  return {
    year: get("year"),
    month: get("month"),
    day: get("day"),
    hour: get("hour"),
    minute: get("minute"),
    second: get("second"),
  };
}

function zonedWallToUtc(
  year: number,
  month: number,
  day: number,
  hour: number,
  minute: number,
  second: number,
  millis: number,
  timeZone: string,
): { instantMs: number; ambiguous: boolean } | null {
  try {
    const utcGuess = Date.UTC(year, month - 1, day, hour, minute, second, millis);
    let offset = getTimeZoneOffsetMs(timeZone, utcGuess);
    let instantMs = utcGuess - offset;
    offset = getTimeZoneOffsetMs(timeZone, instantMs);
    instantMs = utcGuess - offset;

    const wall = wallPartsInZone(instantMs, timeZone);
    const matches =
      wall.year === year &&
      wall.month === month &&
      wall.day === day &&
      wall.hour === hour &&
      wall.minute === minute &&
      wall.second === second;

    if (!matches) {
      // DST gap: wall clock does not exist in this zone.
      return { instantMs, ambiguous: true };
    }

    // DST overlap: another nearby offset may also map to the same wall time.
    for (const probe of [instantMs - 3600_000, instantMs + 3600_000]) {
      const otherOffset = getTimeZoneOffsetMs(timeZone, probe);
      if (otherOffset === offset) continue;
      const alt = utcGuess - otherOffset;
      const altWall = wallPartsInZone(alt, timeZone);
      if (
        altWall.year === year &&
        altWall.month === month &&
        altWall.day === day &&
        altWall.hour === hour &&
        altWall.minute === minute &&
        altWall.second === second
      ) {
        return { instantMs, ambiguous: true };
      }
    }

    return { instantMs, ambiguous: false };
  } catch {
    return null;
  }
}

function truncate(
  instantMs: number,
  precision: TimestampPrecision,
  written: Exclude<TimestampPrecision, "unknown">,
): number {
  const d = new Date(instantMs);
  if (precision === "exact") return instantMs;
  if (precision === "day") {
    return Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), d.getUTCDate());
  }
  if (precision === "month") {
    return Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), 1);
  }
  // Approximate: keep written granularity, never a time of day.
  if (written === "exact" || written === "day") {
    return Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), d.getUTCDate());
  }
  return instantMs;
}

function formatDisplay(
  instantMs: number,
  precision: TimestampPrecision,
  written: Exclude<TimestampPrecision, "unknown">,
  timezoneKnown: boolean,
): string {
  const d = new Date(instantMs);
  if (precision === "exact") {
    return timezoneKnown ? isoZ(d) : isoNaive(d);
  }
  if (precision === "day") {
    return `${pad(d.getUTCFullYear(), 4)}-${pad(d.getUTCMonth() + 1, 2)}-${pad(d.getUTCDate(), 2)}`;
  }
  if (precision === "month") {
    return `${pad(d.getUTCFullYear(), 4)}-${pad(d.getUTCMonth() + 1, 2)}`;
  }
  if (written === "approximate") {
    return pad(d.getUTCFullYear(), 4);
  }
  return written === "month"
    ? `${pad(d.getUTCFullYear(), 4)}-${pad(d.getUTCMonth() + 1, 2)}`
    : `${pad(d.getUTCFullYear(), 4)}-${pad(d.getUTCMonth() + 1, 2)}-${pad(d.getUTCDate(), 2)}`;
}

function isoNaive(d: Date): string {
  let text = `${pad(d.getUTCFullYear(), 4)}-${pad(d.getUTCMonth() + 1, 2)}-${pad(d.getUTCDate(), 2)}T${pad(d.getUTCHours(), 2)}:${pad(d.getUTCMinutes(), 2)}:${pad(d.getUTCSeconds(), 2)}`;
  const ms = d.getUTCMilliseconds();
  if (ms) {
    text += `.${String(ms).padStart(3, "0")}`.replace(/0+$/, "");
  }
  return text;
}

function isoZ(d: Date): string {
  return `${isoNaive(d)}Z`;
}

function pad(n: number, width: number): string {
  return String(n).padStart(width, "0");
}
