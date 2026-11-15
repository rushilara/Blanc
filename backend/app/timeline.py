"""Deterministic timeline normalization (BLA-19).

The LLM writes timeline events; this module, not the LLM, decides what their
timestamps mean and in what order they go. Everything here is a pure function,
so the same events always produce the same timeline, whatever order they
arrive in.

Rules:

- **Never invent precision.** A timestamp is only as precise as what is written:
  `2026-04-10` is a day, `2026-04` a month, `2026` approximate. An event declared
  `exact` with only a date becomes `day`. A declared precision can make a time
  coarser, never finer.
- **Known time zones are converted to UTC.** A numeric offset (`+13:00`), `Z`,
  `UTC`/`GMT`, or an IANA zone name (`Pacific/Auckland`) is a known zone. When a
  time carries both an offset and a bracketed zone name, the offset fixes the
  instant (RFC 9557) and the name is extra information. Offsets beyond +-14:00 are
  not real and are not trusted. A time with no zone is kept as written and flagged
  `timezone_known=False`; it is not assumed to be local or UTC, only placed in order
  as written.
- **Daylight-saving gaps and overlaps are not resolved silently.** A wall-clock time
  that a named zone skips (`02:30` on the spring-forward night) or repeats (`01:30`
  on the fall-back night) has no single instant, so only its day is kept: precision
  `day`.
- **Nothing is guessed.** Text such as `10/04/2026` (day or month first?) or
  `last Friday` is not parsed, and a 10-digit number that also reads as a compact
  date and hour (`2026041009`) is not taken as epoch seconds. Those events, and
  events with no timestamp or a declared precision of `unknown`, go to the separate
  `undated` group.
- **Order is total and stable:** by instant, then finer precision first, then
  event ID and description.

Supported forms: ISO 8601 dates and date-times (`T` or a space between date and
time, optional seconds and fraction), and Unix epoch seconds or milliseconds.
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.contracts import TimelineEvent, TimestampPrecision

# Finest first. `unknown` is not here: an event with unknown precision is undated.
_PRECISION_RANK: dict[TimestampPrecision, int] = {
    "exact": 0,
    "day": 1,
    "month": 2,
    "approximate": 3,
}

_ISO = re.compile(
    r"""^
    (?P<year>\d{4})
    (?:-(?P<month>\d{2})
      (?:-(?P<day>\d{2})
        (?:[T ]
          (?P<hour>\d{2}):(?P<minute>\d{2})
          (?::(?P<second>\d{2})(?:[.,](?P<fraction>\d{1,9}))?)?
          \s*(?P<offset>Z|UTC|GMT|[+-]\d{2}(?::?\d{2})?)?
          (?:\s*\[?(?P<zone>[A-Za-z_]+(?:/[A-Za-z0-9_+\-]+)+)\]?)?
        )?
      )?
    )?
    $""",
    re.VERBOSE | re.IGNORECASE,
)
_EPOCH = re.compile(r"^(?P<whole>\d{10}|\d{13})(?:\.(?P<fraction>\d+))?$")
_EPOCH_YEARS = (1990, 2100)
# A compact date and hour, YYYYMMDDHH, which a 10-digit epoch value can also look like.
_COMPACT_DATE_HOUR = re.compile(
    r"^(?P<year>19\d{2}|20\d{2}|2100)(?P<month>\d{2})(?P<day>\d{2})(?P<hour>\d{2})$"
)
_MAX_OFFSET = timedelta(hours=14)


@dataclass(frozen=True)
class NormalizedTime:
    """What one written timestamp means."""

    # Aware UTC instant used for ordering; None when the event is undated.
    instant: datetime | None
    precision: TimestampPrecision
    # False when the timestamp carried no zone: `instant` then orders it as written.
    timezone_known: bool
    # Normalized text for display, at the precision actually known. None when undated.
    # Show it as text: when timezone_known is False it has no zone (`2026-04-10T09:15:00`),
    # and parsing it (for example with JavaScript's `Date`) would read it as local time.
    display: str | None

    @property
    def is_dated(self) -> bool:
        return self.instant is not None

    def as_dict(self) -> dict[str, Any]:
        return {
            "instant": _iso_z(self.instant) if self.instant is not None else None,
            "precision": self.precision,
            "timezone_known": self.timezone_known,
            "display": self.display,
        }


UNDATED = NormalizedTime(instant=None, precision="unknown", timezone_known=False, display=None)


@dataclass(frozen=True)
class TimelineEntry:
    event: TimelineEvent
    time: NormalizedTime


@dataclass(frozen=True)
class NormalizedTimeline:
    """Dated events in order, and undated or uncertain events kept apart."""

    dated: list[TimelineEntry]
    undated: list[TimelineEntry]

    def as_dict(self) -> dict[str, Any]:
        def entry(item: TimelineEntry) -> dict[str, Any]:
            return {"event_id": item.event.id, **item.time.as_dict()}

        return {
            "dated": [entry(item) for item in self.dated],
            "undated": [entry(item) for item in self.undated],
        }


def normalize_timeline(events: Sequence[TimelineEvent]) -> NormalizedTimeline:
    """Sort events deterministically and separate the ones that cannot be dated."""
    entries = [
        TimelineEntry(event, normalize_timestamp(event.timestamp, event.timestamp_precision))
        for event in events
    ]
    dated = sorted(
        (item for item in entries if item.time.instant is not None),
        key=lambda item: (
            item.time.instant,
            _PRECISION_RANK[item.time.precision],
            item.event.id,
            item.event.description,
        ),
    )
    undated = sorted(
        (item for item in entries if item.time.instant is None),
        key=lambda item: (item.event.id, item.event.description),
    )
    return NormalizedTimeline(dated=dated, undated=undated)


def normalize_timestamp(
    raw: str | None, declared: TimestampPrecision | None = None
) -> NormalizedTime:
    """Interpret one written timestamp. Returns `UNDATED` rather than guessing."""
    if raw is None or declared == "unknown":
        return UNDATED
    text = raw.strip()
    if not text:
        return UNDATED

    parsed = _parse_epoch(text) or _parse_iso(text)
    if parsed is None:
        return UNDATED
    moment, written, timezone_known = parsed

    # A declared precision may only make the time coarser than what is written.
    precision = written
    if declared is not None and _PRECISION_RANK[declared] > _PRECISION_RANK[written]:
        precision = declared
    moment = _truncate(moment, precision, written)
    return NormalizedTime(
        instant=moment,
        precision=precision,
        timezone_known=timezone_known,
        display=_display(moment, precision, written, timezone_known),
    )


def _parse_epoch(text: str) -> tuple[datetime, TimestampPrecision, bool] | None:
    match = _EPOCH.match(text)
    if match is None:
        return None
    whole = match["whole"]
    if _looks_like_compact_date(whole):
        return None  # epoch seconds or a date and hour? Not guessed.
    seconds = int(whole) if len(whole) == 10 else int(whole) / 1000
    if match["fraction"] and len(whole) == 10:
        seconds = float(f"{whole}.{match['fraction']}")
    try:
        moment = datetime.fromtimestamp(seconds, tz=UTC)
    except (OverflowError, OSError, ValueError):
        return None
    if not _EPOCH_YEARS[0] <= moment.year <= _EPOCH_YEARS[1]:
        return None
    return moment, "exact", True


def _looks_like_compact_date(digits: str) -> bool:
    match = _COMPACT_DATE_HOUR.match(digits)
    if match is None:
        return False
    try:
        datetime(int(match["year"]), int(match["month"]), int(match["day"]), int(match["hour"]))
    except ValueError:
        return False
    return True


def _parse_iso(text: str) -> tuple[datetime, TimestampPrecision, bool] | None:
    match = _ISO.match(text)
    if match is None:
        return None
    year = int(match["year"])
    month = int(match["month"]) if match["month"] else 1
    day = int(match["day"]) if match["day"] else 1
    if match["hour"] is None:
        written: TimestampPrecision = (
            "day" if match["day"] else "month" if match["month"] else "approximate"
        )
        try:
            return datetime(year, month, day, tzinfo=UTC), written, False
        except ValueError:
            return None

    fraction = (match["fraction"] or "")[:6].ljust(6, "0")
    try:
        naive = datetime(
            year,
            month,
            day,
            int(match["hour"]),
            int(match["minute"]),
            int(match["second"] or 0),
            int(fraction) if match["fraction"] else 0,
        )
    except ValueError:
        return None

    zone = _zone(match["offset"], match["zone"])
    if zone is None:
        # No zone written: keep the wall-clock time as written and order it as such.
        return naive.replace(tzinfo=UTC), "exact", False
    try:
        if _wall_time_is_ambiguous(naive, zone):
            # Skipped or repeated by a daylight-saving change: only the day is certain.
            day = naive.replace(hour=0, minute=0, second=0, microsecond=0)
            return day.replace(tzinfo=UTC), "day", True
        return naive.replace(tzinfo=zone).astimezone(UTC), "exact", True
    except (OverflowError, ValueError):
        return None


def _wall_time_is_ambiguous(naive: datetime, zone: timezone | ZoneInfo) -> bool:
    """True when a named zone skips or repeats this wall-clock time (fold 0 and 1 differ)."""
    if not isinstance(zone, ZoneInfo):
        return False
    first = naive.replace(tzinfo=zone, fold=0).utcoffset()
    second = naive.replace(tzinfo=zone, fold=1).utcoffset()
    return first != second


def _zone(offset: str | None, name: str | None) -> timezone | ZoneInfo | None:
    """The zone that fixes the instant: a written offset first (RFC 9557), then a name.

    An offset that is not a real one (beyond +-14:00) is not trusted, and neither is a
    name the zone database does not know; either way the other one is used if present.
    """
    fixed = _offset(offset) if offset else None
    if fixed is not None:
        return fixed
    if name:
        try:
            return ZoneInfo(name)
        except (ZoneInfoNotFoundError, ValueError):
            return None
    return None


def _offset(offset: str) -> timezone | None:
    if offset.upper() in ("Z", "UTC", "GMT"):
        return UTC
    sign = -1 if offset[0] == "-" else 1
    digits = offset[1:].replace(":", "")
    hours, minutes = int(digits[:2]), int(digits[2:] or 0)
    delta = timedelta(hours=hours, minutes=minutes)
    if minutes > 59 or delta > _MAX_OFFSET:
        return None
    return timezone(sign * delta)


def _truncate(
    moment: datetime, precision: TimestampPrecision, written: TimestampPrecision
) -> datetime:
    """Drop detail finer than the final precision, so ordering is not falsely precise."""
    if precision == "exact":
        return moment
    if precision == "day":
        return moment.replace(hour=0, minute=0, second=0, microsecond=0)
    if precision == "month":
        return moment.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    # Approximate: keep the granularity that was written, but never a time of day.
    if written in ("exact", "day"):
        return moment.replace(hour=0, minute=0, second=0, microsecond=0)
    return moment


def _display(
    moment: datetime,
    precision: TimestampPrecision,
    written: TimestampPrecision,
    timezone_known: bool,
) -> str:
    if precision == "exact":
        return _iso_z(moment) if timezone_known else _iso_naive(moment)
    if precision == "day":
        return moment.strftime("%Y-%m-%d")
    if precision == "month":
        return moment.strftime("%Y-%m")
    if written == "approximate":
        return moment.strftime("%Y")
    return moment.strftime("%Y-%m" if written == "month" else "%Y-%m-%d")


def _iso_naive(moment: datetime) -> str:
    text = moment.strftime("%Y-%m-%dT%H:%M:%S")
    if moment.microsecond:
        text += f".{moment.microsecond:06d}".rstrip("0")
    return text


def _iso_z(moment: datetime) -> str:
    return _iso_naive(moment) + "Z"
