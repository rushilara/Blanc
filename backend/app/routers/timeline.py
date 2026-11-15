"""Pure timestamp normalization for the timeline view; no case data is queried.

The caller supplies the events. This endpoint only returns their IDs and time
metadata from the existing deterministic normalizer; it never retrieves evidence,
stores a revision or calls a model.
"""

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.contracts import TimelineEvent, TimestampPrecision
from app.timeline import normalize_timeline

router = APIRouter(prefix="/timeline", tags=["timeline"])


class TimelineRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    events: list[TimelineEvent] = Field(max_length=2000)

    @field_validator("events")
    @classmethod
    def unique_ids(cls, events: list[TimelineEvent]) -> list[TimelineEvent]:
        if len({event.id for event in events}) != len(events):
            raise ValueError("event IDs must be unique")
        return events


class TimelineTime(BaseModel):
    event_id: str
    instant: str | None
    precision: TimestampPrecision
    timezone_known: bool
    display: str | None


class TimelineRead(BaseModel):
    dated: list[TimelineTime]
    undated: list[TimelineTime]


@router.post("/normalize")
def normalize_events(body: TimelineRequest) -> TimelineRead:
    return TimelineRead.model_validate(normalize_timeline(body.events).as_dict())
