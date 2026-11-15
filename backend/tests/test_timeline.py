"""Tests for deterministic timeline normalization (BLA-19)."""

import copy
import random
from datetime import UTC, datetime

import pytest
from _paths import RAW

from app.contracts import AnalysisRevision, TimelineEvent
from app.timeline import UNDATED, normalize_timeline, normalize_timestamp


def event(event_id: str, timestamp: str | None, precision: str | None = None) -> TimelineEvent:
    data = {
        "id": event_id,
        "description": f"Event {event_id}",
        "timestamp": timestamp,
        "evidence_ids": ["rec-01"],
        "status": "reported",
    }
    if precision is not None:
        data["timestamp_precision"] = precision
    return TimelineEvent.model_validate(data)


# --- single timestamps -----------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "display"),
    [
        ("2026-04-10T09:15:00Z", "2026-04-10T09:15:00Z"),
        ("2026-04-10 09:15:00Z", "2026-04-10T09:15:00Z"),
        ("2026-04-10T09:15Z", "2026-04-10T09:15:00Z"),
        ("2026-04-10T09:15:00 UTC", "2026-04-10T09:15:00Z"),
        ("2026-04-10T09:15:00+00:00", "2026-04-10T09:15:00Z"),
        ("2026-04-10T04:15:00-05:00", "2026-04-10T09:15:00Z"),
        ("2026-04-10T04:15:00-0500", "2026-04-10T09:15:00Z"),
        ("2026-04-10T22:15:00+13", "2026-04-10T09:15:00Z"),
        ("2026-04-10T09:15:00.250Z", "2026-04-10T09:15:00.25Z"),
    ],
)
def test_known_time_zones_are_converted_to_utc(raw: str, display: str) -> None:
    time = normalize_timestamp(raw)
    assert time.display == display
    assert time.precision == "exact"
    assert time.timezone_known is True
    assert time.instant is not None and time.instant.tzinfo is not None


def test_a_named_zone_is_converted_with_its_daylight_saving_rule() -> None:
    # Auckland is UTC+13 in February (daylight saving) and UTC+12 in July.
    summer = normalize_timestamp("2019-02-01 09:00 Pacific/Auckland")
    winter = normalize_timestamp("2019-07-01 09:00 Pacific/Auckland")
    assert summer.display == "2019-01-31T20:00:00Z"
    assert winter.display == "2019-06-30T21:00:00Z"
    assert summer.timezone_known and winter.timezone_known


def test_a_time_with_no_zone_is_kept_as_written_and_flagged() -> None:
    time = normalize_timestamp("2026-04-10 09:15")
    assert time.display == "2026-04-10T09:15:00"  # no "Z": the zone is not known
    assert time.timezone_known is False
    assert time.precision == "exact"
    assert time.instant == datetime(2026, 4, 10, 9, 15, tzinfo=UTC)


def test_an_unrecognized_zone_name_is_not_treated_as_known() -> None:
    time = normalize_timestamp("2026-04-10 09:15 Mars/Olympus_Mons")
    assert time.timezone_known is False
    assert time.display == "2026-04-10T09:15:00"


@pytest.mark.parametrize(
    ("raw", "precision", "display"),
    [
        ("2026-04-10", "day", "2026-04-10"),
        ("2026-04", "month", "2026-04"),
        ("2026", "approximate", "2026"),
    ],
)
def test_partial_timestamps_keep_only_the_precision_written(
    raw: str, precision: str, display: str
) -> None:
    time = normalize_timestamp(raw)
    assert (time.precision, time.display) == (precision, display)
    assert time.is_dated


def test_epoch_seconds_and_milliseconds_are_exact_utc() -> None:
    assert normalize_timestamp("1775812500").display == "2026-04-10T09:15:00Z"
    assert normalize_timestamp("1775812500123").display == "2026-04-10T09:15:00.123Z"
    # Digits that are not a plausible epoch are not forced into a date.
    assert normalize_timestamp("12345") is UNDATED
    assert normalize_timestamp("9999999999999") is UNDATED


@pytest.mark.parametrize(
    "raw",
    [None, "", "   ", "last Friday", "10/04/2026", "April 10, 2026", "2026-13-40", "2026-02-30"],
)
def test_missing_ambiguous_or_invalid_timestamps_are_undated(raw: str | None) -> None:
    time = normalize_timestamp(raw)
    assert time is UNDATED
    assert time.instant is None and time.display is None and time.precision == "unknown"


def test_precision_is_never_invented() -> None:
    # Declared exact, but only a date was written.
    assert normalize_timestamp("2026-04-10", "exact").precision == "day"
    assert normalize_timestamp("2026-04", "day").precision == "month"
    assert normalize_timestamp("2026", "exact").precision == "approximate"


def test_a_declared_precision_can_make_a_time_coarser() -> None:
    day = normalize_timestamp("2026-04-10T09:15:00Z", "day")
    assert (day.precision, day.display) == ("day", "2026-04-10")
    assert day.instant == datetime(2026, 4, 10, tzinfo=UTC)  # time of day dropped

    month = normalize_timestamp("2026-04-10T09:15:00Z", "month")
    assert (month.precision, month.display) == ("month", "2026-04")
    assert month.instant == datetime(2026, 4, 1, tzinfo=UTC)

    approximate = normalize_timestamp("2026-04-10T09:15:00Z", "approximate")
    assert (approximate.precision, approximate.display) == ("approximate", "2026-04-10")


def test_a_declared_precision_of_unknown_means_undated() -> None:
    assert normalize_timestamp("2026-04-10T09:15:00Z", "unknown") is UNDATED


# --- whole timelines ---------------------------------------------------------------


def mixed_events() -> list[TimelineEvent]:
    return [
        event("e-undated", None),
        event("e-noon-utc", "2026-04-10T12:00:00Z"),
        event("e-morning-nz", "2026-04-10T21:15:00+12:00"),  # 09:15 UTC
        event("e-day", "2026-04-10"),
        event("e-month", "2026-04"),
        event("e-year", "2026"),
        event("e-vague", "sometime last spring"),
        event("e-prior-day-local", "2026-04-09 23:00"),
        event("e-uncertain", "2026-04-10T09:15:00Z", "unknown"),
        event("e-epoch", "1775812500"),  # 09:15 UTC, same instant as e-morning-nz
    ]


def test_events_are_sorted_and_undated_ones_kept_apart() -> None:
    timeline = normalize_timeline(mixed_events())
    assert [item.event.id for item in timeline.dated] == [
        "e-year",  # 2026-01-01, approximate
        "e-month",  # 2026-04-01, month
        "e-prior-day-local",
        "e-day",  # 2026-04-10 00:00, day
        "e-epoch",  # same instant as e-morning-nz: tie broken by ID
        "e-morning-nz",
        "e-noon-utc",
    ]
    assert [item.event.id for item in timeline.undated] == [
        "e-uncertain",
        "e-undated",
        "e-vague",
    ]
    assert all(item.time.instant is None for item in timeline.undated)


def test_the_order_does_not_depend_on_the_input_order() -> None:
    expected = normalize_timeline(mixed_events()).as_dict()
    shuffler = random.Random(7)
    for _ in range(25):
        shuffled = mixed_events()
        shuffler.shuffle(shuffled)
        assert normalize_timeline(shuffled).as_dict() == expected


def test_at_the_same_instant_finer_precision_sorts_first() -> None:
    timeline = normalize_timeline(
        [
            event("a-month", "2026-04"),
            event("b-day", "2026-04-01"),
            event("c-exact", "2026-04-01T00:00:00Z"),
        ]
    )
    assert [item.event.id for item in timeline.dated] == ["c-exact", "b-day", "a-month"]


def test_events_are_never_dropped_or_duplicated() -> None:
    events = mixed_events()
    timeline = normalize_timeline(events)
    seen = [item.event.id for item in (*timeline.dated, *timeline.undated)]
    assert sorted(seen) == sorted(e.id for e in events)
    assert normalize_timeline([]).as_dict() == {"dated": [], "undated": []}


def test_as_dict_is_json_ready() -> None:
    timeline = normalize_timeline([event("e1", "2026-04-10T04:15:00-05:00"), event("e2", None)])
    assert timeline.as_dict() == {
        "dated": [
            {
                "event_id": "e1",
                "instant": "2026-04-10T09:15:00Z",
                "precision": "exact",
                "timezone_known": True,
                "display": "2026-04-10T09:15:00Z",
            }
        ],
        "undated": [
            {
                "event_id": "e2",
                "instant": None,
                "precision": "unknown",
                "timezone_known": False,
                "display": None,
            }
        ],
    }


def test_the_synthetic_revision_fixture_normalizes_cleanly() -> None:
    revision = AnalysisRevision.model_validate(copy.deepcopy(RAW["revision"]))
    timeline = normalize_timeline(revision.timeline_events)
    assert [item.event.id for item in timeline.dated] == ["event-1", "event-2"]
    assert [item.time.display for item in timeline.dated] == [
        "2026-04-10T09:15:00Z",
        "2026-04-10T10:30:00Z",
    ]
    assert timeline.undated == []
